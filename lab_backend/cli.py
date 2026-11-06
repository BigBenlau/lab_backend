"""離線維運指令。沒有預設密碼；不啟動容器或 HTTP 服務。"""
import argparse
import base64
import getpass
import hashlib
import json
import uuid
from contextlib import contextmanager
import io
import os
from pathlib import Path
import sys
import zipfile
from datetime import datetime, timedelta, timezone

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

from .config import Config
from .models import FILES, MODELS, new_metadata, validate_state
from .auth import NewLocalUser, audit, build_user, strong_password
from .store import (DB_VERSION, Store, decode_csv, encode_csv, exclusive_lock, json_loads,
                    read_file, write_new, validate_capacity, sync_dir)

BACKUP_MAGIC = f'LABSQL{DB_VERSION}\0'.encode()


def operator():
    """以 OS 操作者名稱標記離線維運，與網站帳戶 ID 區分。"""
    return 'cli:' + getpass.getuser()


def password_prompt(label='新密碼'):
    """隱藏輸入並重複確認密碼，不提供預設值。"""
    password = strong_password(getpass.getpass(f'{label}（至少 15 字元）: '))
    if password != getpass.getpass('再次輸入: '):
        raise ValueError('兩次輸入不同')
    return password


def init(store):
    """只建立空資料庫；實際內容由管理表單或離線CSV匯入。"""
    store.initialize()
    print('已建立空SQLite五張表。下一步執行create-admin及import-csv。')


def local_admin(store, recover=False):
    """停服鎖下建立/恢復具名本地管理員，共用網頁的帳戶型別與密碼組裝。"""
    with store.maintenance():
        login = input('具名管理員登入名稱（小寫）: ').strip()
        name = input('顯示名稱: ').strip()
        user = build_user(NewLocalUser(displayName=name, login=login, password=password_prompt()))
        def operation(state):
            """建立唯一登入身份，或更新既有憑證並提升授權版本。"""
            existing = next((u for u in state['users'] if u['login'] == login), None)
            if recover:
                if not existing:
                    raise ValueError('找不到本地帳戶；建立請用 create-admin')
                existing.update(displayName=name, passwordHash=user['passwordHash'],
                                active=True, authVersion=existing['authVersion'] + 1, version=existing['version'] + 1)
                target = existing['id']
            else:
                if existing:
                    raise ValueError('登入名稱已存在')
                state['users'].append(user)
                target = user['id']
            audit(state, operator(), 'user.recovered' if recover else 'user.created', target)
        store.change(operation, resources=('users',))
    print('管理員已保存，可使用帳號密碼登入。')


def import_csv(store, directory: Path, apply=False):
    """離線整批新增內容；預設只預覽，套用時重新驗證。"""
    with store.maintenance():
        if not directory.is_dir() or directory.is_symlink():
            raise ValueError('請指定含內容 CSV 的普通目錄')
        allowed = {FILES[k]: k for k in MODELS}
        incoming = {}
        for path in sorted(directory.iterdir()):
            if path.name not in allowed or path.is_symlink() or not path.is_file():
                raise ValueError('目錄只能含 3 類內容 CSV，不接受帳戶、審計或其他檔案')
            incoming[allowed[path.name]] = decode_csv(allowed[path.name], read_file(path), private=False)
        if not incoming:
            raise ValueError('沒有可匯入 CSV')
        counts = {'added': 0, 'skipped': 0}
        def operation(state):
            """略過既有 ID、拒絕同批重複 ID，新增內容儲存後立即可公開讀取。"""
            counts.update(added=0, skipped=0)
            for resource, rows in incoming.items():
                ids = {r['id'] for r in state[resource]}
                batch_ids = set()
                for raw in rows:
                    data = MODELS[resource].model_validate(raw).model_dump()
                    if data['id'] in batch_ids:
                        raise ValueError('同批 CSV 有重複 ID')
                    batch_ids.add(data['id'])
                    if data['id'] in ids:
                        counts['skipped'] += 1
                        continue
                    state[resource].append({**data, **new_metadata(operator())})
                    counts['added'] += 1
            audit(state, operator(), 'content.import', f"added={counts['added']};skipped={counts['skipped']}")
        if apply:
            # change會在寫交易內驗證；預覽才需在此驗證，避免套用時重複掃描。
            store.change(operation)
        else:
            state = store.read()
            operation(state)
            validate_state(state)
            validate_capacity(state)
        print(('已套用' if apply else '僅預覽，未寫入'), counts)
        if not apply:
            print('新增內容套用後立即可公開讀取；確認後加 --apply，套用時重新驗證。')


def backup_key(password: str, salt: bytes):
    """從獨立備份密碼與隨機 salt 衍生金鑰，不重用登入密碼雜湊。"""
    derived = Scrypt(salt=salt, length=32, n=2**17, r=8, p=1).derive(password.encode())
    return Fernet(base64.urlsafe_b64encode(derived))


def file_hash(path):
    """串流計算檔案指紋；版本清單只記指紋，不記密碼或私有檔案內容。"""
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(descriptor, 'rb') as handle:
        return hashlib.file_digest(handle, 'sha256').hexdigest()


def content_hash(state):
    """保存內容庫的對應版本；保持入庫順序，靜態備份據此核對資料快照。"""
    data = {key: state[key] for key in MODELS}
    return hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def atomic_json(path, data):
    """私有監測狀態以新檔＋原子替換寫入，避免斷途中留下半份JSON。"""
    pending = path.with_name('.' + path.name + '.' + uuid.uuid4().hex)
    try:
        write_new(pending, json.dumps(data, ensure_ascii=False).encode())
        os.replace(pending, path)
        sync_dir(path.parent)
    finally:
        pending.unlink(missing_ok=True)


def backup_state(store):
    """不存在表示尚未備份；損壞狀態明確報錯，監測不得把它視為成功。"""
    path = store.admin_path.parent / 'backup-status.json'
    data = json_loads(read_file(path)) if path.exists() else {}
    if not isinstance(data, dict):
        raise ValueError('備份狀態無效')
    return data


@contextmanager
def backup_job(store, kind, version):
    """記錄成功與失敗；排程可檢查上次成功時間，不依賴終端輸出。"""
    from .models import now
    if not version or len(version) > 100 or not version.isascii() or not all(c.isalnum() or c in '._-' for c in version):
        raise ValueError('備份版本須為1–100字元的英數字、點、底線或連字號')
    # 序列化兩種備份的狀態寫入，並避免監測讀到部分狀態。
    with exclusive_lock(store.admin_path.parent / 'backup.lock', create=True):
        state = backup_state(store)
        prior = state.get(kind, {})
        entry = {'version': version, 'at': now(), 'ok': False, 'lastSuccessAt': prior.get('lastSuccessAt')}
        try:
            yield entry
        except Exception as exc:
            entry['errorType'] = type(exc).__name__
            state[kind] = entry
            atomic_json(store.admin_path.parent / 'backup-status.json', state)
            print('ALERT backup_failed kind=' + kind, file=sys.stderr)
            raise
        else:
            entry.update(ok=True, lastSuccessAt=now())
            state[kind] = entry
            atomic_json(store.admin_path.parent / 'backup-status.json', state)


def backup(store, output: Path, version=None):
    """完整已提交資料的加密備份，成功後留下可監測的版本與指紋。"""
    with backup_job(store, 'database', version or uuid.uuid4().hex) as record:
        record.update(_database_backup(store, output))
    print('已產生加密備份；請異地保存，密碼另行保管。')


def _database_backup(store, output: Path):
    """讀取同一跨庫交易快照，以 CSV 交換格式加密打包。"""
    password = password_prompt('備份加密密碼')
    state = store.read(include_audit=True)
    validate_capacity(state)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for resource, name in FILES.items():
            archive.writestr(name, encode_csv(resource, state[resource]))
        if sum(info.file_size for info in archive.infolist()) > 256 * 1024 * 1024:
            raise ValueError('備份明文超限，請先封存歷史審計')
    salt = os.urandom(16)
    payload = BACKUP_MAGIC + salt + backup_key(password, salt).encrypt(buffer.getvalue())
    if len(payload) > 256 * 1024 * 1024:
        raise ValueError('備份超限')
    write_new(output, payload)
    sync_dir(output.parent)
    return {'sha256': file_hash(output), 'contentHash': content_hash(state)}


def read_backup(path: Path, password: str):
    """只接受本版加密備份，先驗項目/大小/完整資料，不解壓到磁碟。"""
    if path.is_symlink() or path.stat().st_size > 256 * 1024 * 1024:
        raise ValueError('備份檔案不合法/過大')
    raw = path.read_bytes()
    if not raw.startswith(BACKUP_MAGIC):
        raise ValueError('備份格式不符；此 demo 僅接受本版新備份')
    offset = len(BACKUP_MAGIC)
    payload = backup_key(password, raw[offset:offset + 16]).decrypt(raw[offset + 16:])
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        infos = archive.infolist()
        expected = set(FILES.values())
        if len(infos) != len(expected) or set(archive.namelist()) != expected:
            raise ValueError('備份內容不完整/重複/多餘檔案')
        if any(i.file_size > 32 * 1024 * 1024 for i in infos) or sum(i.file_size for i in infos) > 256 * 1024 * 1024:
            raise ValueError('備份內容超限')
        state = {key: decode_csv(key, archive.read(name).decode('utf-8')) for key, name in FILES.items()}
    validate_state(state)
    validate_capacity(state)
    return state


def restore(store, path: Path, apply=False):
    """先預覽驗證，明確套用才替換資料並撤銷舊登入版本。"""
    password = getpass.getpass('備份密碼: ')
    state = read_backup(path, password)
    for user in state['users']:
        user['authVersion'] += 1
        user['version'] += 1
    audit(state, operator(), 'storage.restored')
    with store.maintenance():
        if apply:
            # 內容、帳戶與審計在同一交易替換，不接受舊版備份。
            store.replace(state)
        else:
            validate_state(state)
            validate_capacity(state)
        print('已恢復' if apply else '備份可解析且結構驗證完成；未恢復，確認後加 --apply')


def prune_audit(store, output: Path, keep_days=90):
    """先完整匯出，再移除超過保留天數的線上審計。"""
    if keep_days < 0:
        raise ValueError('keep-days 不可為負數')
    cutoff = datetime.now(timezone.utc) - timedelta(days=keep_days)
    with store.maintenance():
        store.export_audit(output, cutoff=cutoff)
    print('已先完整匯出再封存線上審計；請將匯出檔移至受控異機儲存。')


def main():
    """解析離線子指令；錯誤只顯示類型，避免洩漏憑證輸入。"""
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('init')
    for name in ('create-admin', 'recover-admin'):
        sub.add_parser(name)
    template = sub.add_parser('csv-templates')
    template.add_argument('directory', type=Path)
    imp = sub.add_parser('import-csv')
    imp.add_argument('directory', type=Path)
    imp.add_argument('--apply', action='store_true')
    bak = sub.add_parser('backup')
    bak.add_argument('file', type=Path)
    bak.add_argument('--version', help='本次資料庫與靜態檔共用的備份版本')
    res = sub.add_parser('restore')
    res.add_argument('file', type=Path)
    res.add_argument('--apply', action='store_true')
    exp = sub.add_parser('export-audit')
    exp.add_argument('file', type=Path)
    prune = sub.add_parser('prune-audit')
    prune.add_argument('export_file', type=Path)
    prune.add_argument('--keep-days', type=int, default=90)
    args = parser.parse_args()
    store = Store(Config.load().storage)
    try:
        match args.command:
            case 'init': init(store)
            case 'create-admin': local_admin(store)
            case 'recover-admin': local_admin(store, recover=True)
            case 'import-csv': import_csv(store, args.directory, args.apply)
            case 'backup': backup(store, args.file, args.version)
            case 'restore': restore(store, args.file, args.apply)
            case 'prune-audit': prune_audit(store, args.export_file, args.keep_days)
            case 'export-audit':
                store.export_audit(args.file)
                print('已匯出審計，請存於受控異機位置；CSV 不可信文字勿以試算表執行公式。')
            case 'csv-templates':
                args.directory.mkdir(parents=True, exist_ok=True)
                for resource in MODELS:
                    write_new(args.directory / FILES[resource], encode_csv(resource, [], private=False).encode('utf-8'))
                print('已產生空標頭模板，未匯入內容。')
    except Exception as exc:
        # 不列出 traceback/原始驗證輸入，以免 CLI 將 passwordHash 或密鑰寫到日誌。
        print(f'操作失敗（{type(exc).__name__}）。檢查輸入、格式、權限、密碼及服務是否已停止。', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
