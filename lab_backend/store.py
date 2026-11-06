"""SQLite 儲存與 CSV 交換：內容只存一份，帳戶/審計另檔隔離。

每次操作建立短生命週期連線。管理寫入以同一個 ATTACH 交易提交兩庫，
使用 DELETE journal + FULL synchronous，不能改成 WAL 或關閉同步。
公開入口只開 content.db 的 mode=ro 連線，不載入 admin.db。
"""
from contextlib import contextmanager
from functools import cache
from pathlib import Path
import copy
import csv
import fcntl
import io
import json
import os
import shutil
import sqlite3

from .models import ALL_MODELS, FILES, META, MODELS, ROW_LIMITS, empty_state, person_sort_key, public_row, data_only, validate_state

csv.field_size_limit(1_000_000)
MAX_FILE = 32 * 1024 * 1024
MAX_REQUEST = 256 * 1024
DB_VERSION = 9
APP_ID = 0x4C414231
TABLES = {key: name.removesuffix('.csv') for key, name in FILES.items()}


class StoreError(Exception):
    """對外只回泛化儲存錯誤，不輸出資料庫路徑或 SQL。"""


# ── CSV 僅用於匯入/匯出及加密備份的交換內容。 ──
@cache
def schema_properties(resource):
    """重用固定模型規格；不快取任何資料庫內容。"""
    return ALL_MODELS[resource].model_json_schema()['properties']


def json_loads(text):
    """拒絕重複鍵及 NaN 等非標準值，避免資料被不同解析器解讀成不同內容。"""
    def unique(pairs):
        """同一物件內的鍵必須唯一，不能以後值默默覆蓋前值。"""
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('重複 JSON 欄位')
            result[key] = value
        return result
    def reject(value):
        """拒絕非有限數字。"""
        raise ValueError('不允許非有限數字')
    return json.loads(text, object_pairs_hook=unique, parse_constant=reject)


def columns(resource, private=True):
    """一般 CSV 只含業務欄位；SQL／備份才包含合併歷史與版本。"""
    if not private and resource in MODELS:
        return list(MODELS[resource].model_fields)
    return list(ALL_MODELS[resource].model_fields) + (list(META) if private and resource in MODELS else [])


def field_type(resource, key):
    """把模型規格歸為文字、數字、布林或巢狀 JSON，供兩種編碼共用。"""
    field = schema_properties(resource).get(key, {'type': 'integer' if key == '_version' else 'string'})
    variants = field.get('anyOf', [field])
    kind = next((v.get('type', 'object') for v in variants if v.get('type') != 'null'), 'null')
    return kind, any(v.get('type') == 'null' for v in variants)


def encode_csv(resource, rows, private=True):
    """以 QUOTE_NOTNULL 保留 null/空字串差別，巢狀欄位寫成 JSON 儲存格。"""
    buffer = io.StringIO(newline='')
    writer = csv.DictWriter(buffer, fieldnames=columns(resource, private), quoting=csv.QUOTE_NOTNULL)
    writer.writeheader()
    for row in rows:
        writer.writerow({k: json.dumps(v, ensure_ascii=False, allow_nan=False, separators=(',', ':'))
                         if isinstance(v, (dict, list, bool, int, float)) else v for k, v in row.items()})
    return buffer.getvalue()


def decode_csv(resource, text, private=True):
    """驗標頭、列數與欄位型別；跨記錄關聯由整批提交再驗證。"""
    if len(text.encode('utf-8')) > MAX_FILE:
        raise ValueError('CSV 超過 32 MiB')
    missing = object()
    reader = csv.DictReader(io.StringIO(text, newline=''), quoting=csv.QUOTE_NOTNULL, strict=True, restval=missing)
    if reader.fieldnames != columns(resource, private):
        raise ValueError(f'{resource} CSV 標頭/順序不符合契約，請重新產生模板')
    rows = []
    for raw in reader:
        if None in raw or any(v is missing for v in raw.values()):
            raise ValueError('CSV 列欄位數不符')
        rows.append({k: v if v is None or field_type(resource, k)[0] == 'string' else json_loads(v)
                     for k, v in raw.items()})
        limit = ROW_LIMITS.get(resource)
        if limit is not None and len(rows) > limit:
            raise ValueError('CSV 行數超限')
    return rows


def json_size(value):
    """計算與讀取保護相同的UTF-8大小，不以Python字元數代替位元組。"""
    return len(json.dumps(value, ensure_ascii=False, allow_nan=False).encode('utf-8'))


def validate_capacity(state):
    """提交前守住讀取／CSV恢復邊界；審計仍可串流封存，不設累積筆數上限。"""
    for resource, rows in state.items():
        if sum(json_size(row) for row in rows) > MAX_FILE:
            raise ValueError(f'{resource} 超過資料大小上限，未提交；請先減少內容')
        if len(encode_csv(resource, rows).encode('utf-8')) > MAX_FILE:
            raise ValueError(f'{resource} 超過交換檔大小上限，未提交')
        if resource in MODELS:
            for row in rows:
                # CSV匯入也必須可由HTTP PUT編輯；留4KiB給版本及JSON包裝。
                if json_size(data_only(resource, row)) > MAX_REQUEST - 4096:
                    raise ValueError(f'{resource} 單筆過大，將無法在管理頁編輯，未提交')


def write_new(path, data, mode=0o600):
    """建立並同步全新私有檔，拒絕覆寫或跟隨符號連結。"""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
    with os.fdopen(fd, 'wb') as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())


def read_file(path):
    """以大小上限讀取普通 UTF-8 交換檔，不跟隨符號連結。"""
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, 'rb') as handle:
        data = handle.read(MAX_FILE + 1)
    if len(data) > MAX_FILE:
        raise ValueError('檔案超限')
    return data.decode('utf-8')


def sync_dir(path):
    """同步匯出檔的目錄項目，成功後才能清理線上審計。"""
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


@contextmanager
def exclusive_lock(path, *, create=False):
    """管理服務與維運共用非阻塞檔案鎖；關閉即釋放，不刪鎖檔或跟隨符號連結。"""
    flags = os.O_NOFOLLOW | (os.O_CREAT | os.O_RDWR if create else os.O_RDONLY)
    fd = os.open(path, flags, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        os.close(fd)


# ── SQL 識別字只能來自模型；使用者的值一律用參數綁定。 ──
def table_schema(resource):
    """由已確認的模型建立 STRICT 表，避免另外維護一份重複欄位定義。"""
    definitions = []
    for key in columns(resource):
        kind, nullable = field_type(resource, key)
        sql_type = 'INTEGER' if kind in ('integer', 'boolean') else 'REAL' if kind == 'number' else 'TEXT'
        item = f'"{key}" {sql_type}' + ('' if nullable else ' NOT NULL')
        if key == 'id':
            item += ' PRIMARY KEY'
        if key == 'login':
            item += ' UNIQUE'
        if kind == 'boolean':
            item += f' CHECK ("{key}" IN (0, 1))'
        # 巢狀 JSON 由 Pydantic 驗證，不依賴系統 SQLite JSON 擴充的安全旗標差異。
        if resource == 'users' and key == 'role':
            item += ' CHECK ("role" = \'admin\')'
        definitions.append(item)
    return ', '.join(definitions)


class Store:
    """三張內容表共用 content.db；users/audit 位於私有 admin.db。"""
    def __init__(self, root: Path, *, public=False):
        """只保存路徑；匯入程式或建立 app 都不初始化資料庫。"""
        self.root = root.absolute()
        self.public = public
        self.content_path = self.root / 'content' / 'content.db'
        self.admin_path = self.root / 'private' / 'admin.db'

    @contextmanager
    def maintenance(self):
        """管理服務與離線工具互斥；鎖在私有目錄，公開服務不需要它。"""
        if self.public:
            raise StoreError('公開入口不可執行維運')
        try:
            with exclusive_lock(self.admin_path.parent / 'admin.lock'):
                yield
        except OSError as exc:
            raise StoreError('管理服務尚未停止，或維運檔案不可用') from exc

    @contextmanager
    def connection(self, *, write=False, content=True, initializing=False):
        """每次建立交易；公開 mode=ro，寫入兩庫用同一 ATTACH/BEGIN IMMEDIATE。"""
        if self.public and write:
            raise StoreError('公開服務不可寫入')
        primary = self.content_path if self.public else self.admin_path
        paths = [primary] + ([self.content_path] if content and not self.public else [])
        conn = None
        try:
            for path in paths:
                if path.is_symlink() or path.parent.is_symlink() or not path.is_file():
                    raise StoreError('資料庫不存在或不是普通檔案，請先 init')
            mode = 'rw' if write else 'ro'
            conn = sqlite3.connect(primary.as_uri() + '?mode=' + mode, uri=True, timeout=5, isolation_level=None)
            conn.row_factory = sqlite3.Row
            conn.execute('PRAGMA trusted_schema=OFF')
            conn.execute('PRAGMA foreign_keys=ON')
            if len(paths) == 2:
                conn.execute('ATTACH DATABASE ? AS content', (self.content_path.as_uri() + '?mode=' + mode,))
            schemas = ['main'] + (['content'] if len(paths) == 2 else [])
            for schema in schemas:
                if conn.execute(f'PRAGMA {schema}.journal_mode').fetchone()[0] != 'delete':
                    raise StoreError('兩庫原子交易只允許 DELETE journal，不可使用 WAL/OFF')
                conn.execute(f'PRAGMA {schema}.synchronous=FULL')
                if not initializing and (conn.execute(f'PRAGMA {schema}.user_version').fetchone()[0] != DB_VERSION
                                        or conn.execute(f'PRAGMA {schema}.application_id').fetchone()[0] != APP_ID):
                    raise StoreError('資料庫版本不符；此 demo 僅支援本版新建資料庫')
            if not write:
                conn.execute('PRAGMA query_only=ON')
            conn.execute('BEGIN IMMEDIATE' if write else 'BEGIN')
            if self.public:
                # 先取得讀鎖，再檢查日誌，避免檢查與實際讀取之間寫者已改動檔案。
                # 公開容器看不到私有 super-journal，不能自行判定殘留交易已提交。
                conn.execute('SELECT name FROM main.sqlite_schema LIMIT 1').fetchone()
                if self.content_path.with_name('content.db-journal').exists():
                    raise StoreError('內容交易日誌尚未清理，請等待管理服務恢復')
            elif not initializing:
                # 先固定主庫讀取版本，跨庫備份不能一半讀到更新前、一半讀到更新後。
                conn.execute('SELECT id FROM main.users LIMIT 1').fetchone()
            yield conn
            conn.commit()
        except sqlite3.Error as exc:
            raise StoreError('資料庫忙碌、損壞或提交失敗') from exc
        finally:
            if conn is not None:
                conn.close()  # 尚未提交的交易在關閉連線時回滾。

    def table(self, resource):
        """只接受固定資源名稱，公開入口不能取得管理表的 SQL 名稱。"""
        if resource not in TABLES or (self.public and resource not in MODELS):
            raise StoreError('此入口不可讀取要求資料')
        schema = 'content' if resource in MODELS and not self.public else 'main'
        return f'{schema}."{TABLES[resource]}"'

    def read_rows(self, conn, resource, *, suffix='', parameters=()):
        """解碼並驗證儲存模型；公開投影另以白名單隱藏操作者。"""
        names = list(ALL_MODELS[resource].model_fields) if self.public else columns(resource)
        selection = ', '.join(f'"{k}"' for k in names)
        rows, size = [], 0
        for raw in conn.execute(f'SELECT {selection} FROM {self.table(resource)} {suffix}', parameters):
            try:
                row = dict(raw)
                for key, value in row.items():
                    kind, _ = field_type(resource, key)
                    if value is not None and kind in ('array', 'object'):
                        row[key] = json_loads(value)
                    elif value is not None and kind == 'boolean':
                        if value not in (0, 1):
                            raise ValueError('布林欄位損壞')
                        row[key] = bool(value)
                ALL_MODELS[resource].model_validate({k: v for k, v in row.items() if k not in META})
            except (ValueError, TypeError) as exc:
                raise StoreError('資料欄位損壞') from exc
            size += json_size(row)
            limit = ROW_LIMITS.get(resource)
            if size > MAX_FILE or (limit is not None and len(rows) >= limit):
                raise StoreError('資料讀取超限；請先匯出封存歷史審計')
            rows.append(row)
        return rows

    def read(self, resources=None, *, include_audit=False):
        """每次查詢資料庫的新交易，不以記憶體或靜態 JSON 代替資料來源。"""
        wanted = tuple(resources) if resources is not None else tuple(MODELS if self.public else (k for k in ALL_MODELS if k != 'audit'))
        allowed = set(MODELS) if self.public else set(ALL_MODELS) - {'audit'}
        if not set(wanted) <= allowed or (self.public and include_audit):
            raise StoreError('此入口不可讀取要求資料')
        with self.connection(content=bool(set(wanted) & MODELS.keys())) as conn:
            # 呼叫端明確列出本頁所需資源；需要作者反查時同次指定 people 與 publications。
            state = {key: self.read_rows(conn, key, suffix='ORDER BY rowid') for key in wanted}
            if 'people' in state:
                state['people'].sort(key=person_sort_key)
            if not self.public:
                state['audit'] = self.read_rows(conn, 'audit', suffix='ORDER BY rowid') if include_audit else []
            else:
                state = {key: [public_row(key, row) for row in (reversed(state[key]) if key == 'publications' else state[key])] for key in wanted}
            return state

    def insert_rows(self, conn, resource, rows, *, update=False):
        """僅綁定資料值；既有內容用 UPSERT 保留行順序，審計只能新增。"""
        names = columns(resource)
        fields = ', '.join(f'"{k}"' for k in names)
        sql = f'INSERT INTO {self.table(resource)} ({fields}) VALUES ({", ".join("?" for _ in names)})'
        if update:
            key = 'id'
            sql += f' ON CONFLICT ("{key}") DO UPDATE SET ' + ', '.join(f'"{k}"=excluded."{k}"' for k in names if k != key)
        for data in rows:
            values = [json.dumps(data[k], ensure_ascii=False, allow_nan=False, separators=(',', ':'))
                      if isinstance(data[k], (dict, list)) else data[k] for k in names]
            conn.execute(sql, values)

    def save_changes(self, conn, state, before):
        """只更新改變的記錄，內容變更與新增審計同一交易成功或回滾。"""
        for resource, rows in state.items():
            if resource == 'audit':
                self.insert_rows(conn, resource, rows)
                continue
            previous = {r['id']: r for r in before[resource]}
            current = {r['id']: r for r in rows}
            for record_id in previous.keys() - current.keys():
                conn.execute(f'DELETE FROM {self.table(resource)} WHERE id=?', (record_id,))
            self.insert_rows(conn, resource, [r for ident, r in current.items() if r != previous.get(ident)], update=True)

    def change(self, operation, *, resources=None):
        """寫入前先取得 SQLite 寫鎖，再重查權限/版本/關聯，最後一起提交。"""
        wanted = tuple(resources) if resources is not None else tuple(k for k in ALL_MODELS if k != 'audit')
        if not set(wanted) <= set(ALL_MODELS) - {'audit'}:
            raise StoreError('無效交易資源')
        with self.connection(write=True) as conn:
            state = {key: self.read_rows(conn, key, suffix='ORDER BY rowid') for key in wanted}
            state['audit'] = []
            before = copy.deepcopy(state)
            result = operation(state)
            shadow = empty_state()
            shadow.update(state)
            validate_state(shadow)
            validate_capacity(state)
            self.save_changes(conn, state, before)
            return result

    def replace(self, state):
        """離線恢復：完整驗證後於同一交易替換兩庫，失敗時保留原資料。"""
        validate_state(state)
        validate_capacity(state)
        with self.connection(write=True) as conn:
            for resource in ALL_MODELS:
                conn.execute(f'DELETE FROM {self.table(resource)}')
                self.insert_rows(conn, resource, state[resource])

    def read_audit(self, offset=0, limit=100):
        """資料庫只取所需審計頁；累積量是警示，不是停止登入的硬上限。"""
        if self.public or offset < 0 or not 1 <= limit <= 200:
            raise StoreError('不允許的審計查詢')
        with self.connection(content=False) as conn:
            total = conn.execute('SELECT count(*) FROM main.audit').fetchone()[0]
            rows = self.read_rows(conn, 'audit', suffix='ORDER BY rowid DESC LIMIT ? OFFSET ?', parameters=(limit, offset))
        free = min(shutil.disk_usage(path.parent).free for path in (self.content_path, self.admin_path))
        return {'items': rows, 'total': total, 'offset': offset,
                'warnings': (['審計已累積 100,000 筆，請匯出封存'] if total >= 100000 else [])
                            + (['剩餘空間低於 256 MiB'] if free < 256 * 1024 * 1024 else [])}

    def export_audit(self, output, *, cutoff=None):
        """串流匯出避免歷史量受記憶體限制；指定 cutoff 時先落盤再於交易內清理。"""
        from .auth import audit
        if self.public:
            raise StoreError('公開入口不可匯出審計')
        with self.connection(write=cutoff is not None, content=cutoff is not None) as conn:
            fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, 'w', encoding='utf-8', newline='') as handle:
                writer = csv.DictWriter(handle, fieldnames=columns('audit'), quoting=csv.QUOTE_NOTNULL)
                writer.writeheader()
                for raw in conn.execute('SELECT id, at, actor, action, target, result FROM main.audit ORDER BY rowid'):
                    row = dict(raw)
                    ALL_MODELS['audit'].model_validate(row)
                    writer.writerow(row)
                handle.flush()
                os.fsync(handle.fileno())
            sync_dir(output.parent)
            if cutoff is not None:
                deleted = conn.execute('DELETE FROM main.audit WHERE julianday(at) < julianday(?)', (cutoff.isoformat(),)).rowcount
                event = {'audit': []}
                audit(event, 'cli', 'audit.archived', f'removed={deleted}')
                self.insert_rows(conn, 'audit', event['audit'])

    def recover_journals(self):
        """管理程序啟動時以可寫連線讓 SQLite 恢復未完成交易；公開程序不恢復。"""
        with self.connection(write=True) as conn:
            # 重寫已驗證的格式版本表頭，讓 SQLite 正常清理非 hot 日誌。
            # 同值 UPDATE 可能被略過；此處不更改業務資料，也不手動刪檔。
            conn.execute(f'PRAGMA content.user_version={DB_VERSION}')

    def initialize(self, initial_state=None):
        """建立空庫或呼叫端明確提供的資料集；拒絕覆寫，不讀取參考檔。"""
        if self.public:
            raise StoreError('公開服務不可初始化')
        if self.root.exists() and any(self.root.iterdir()):
            raise StoreError('資料目錄非空，拒絕覆寫；舊 CSV 需另行匯入新目錄')
        state = empty_state() if initial_state is None else initial_state
        validate_state(state)
        validate_capacity(state)
        self.root.mkdir(parents=True, mode=0o700, exist_ok=True)
        for path in (self.content_path, self.admin_path):
            path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
            write_new(path, b'')
        write_new(self.admin_path.parent / 'admin.lock', b'')
        with self.maintenance(), self.connection(write=True, initializing=True) as conn:
            for resource in ALL_MODELS:
                conn.execute(f'CREATE TABLE {self.table(resource)} ({table_schema(resource)}) STRICT')
            for schema in ('main', 'content'):
                conn.execute(f'PRAGMA {schema}.application_id={APP_ID}')
                conn.execute(f'PRAGMA {schema}.user_version={DB_VERSION}')
            for resource in ALL_MODELS:
                self.insert_rows(conn, resource, state[resource])
        for path in (self.content_path, self.admin_path):
            sync_dir(path.parent)
