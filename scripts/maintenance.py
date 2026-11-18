"""容器內的檔案檢查、靜態備份及監測；資料匯入統一使用lab_backend.cli。"""
from pathlib import Path, PurePosixPath
from datetime import datetime, timezone
import argparse
import hashlib
import json
import os
import shutil
import stat
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lab_backend.cli import atomic_json, backup_job, backup_state, content_hash, file_hash
from lab_backend.config import Config
from pydantic import Field
from lab_backend.models import Id, Link, Model, Short, now
from lab_backend.store import Store, exclusive_lock, json_loads, read_file, sync_dir, write_new

MAX_STATIC_FILE = 256 * 1024 * 1024
MAX_STATIC_TOTAL = 2 * 1024 * 1024 * 1024
MAX_STATIC_FILES = 10000
MAX_SITE_FILE = 1024 * 1024
STATIC_SUFFIXES = {'.svg', '.png', '.jpg', '.jpeg', '.webp', '.pdf'}


class SiteSettings(Model):
    """固定頁面文字的白名單；不保存成員、論文及管理設定。"""
    name: Short
    fullName: Short = ''
    institution: Short = ''
    introduction: str = ''
    aboutMarkdown: str = ''
    recruitmentMarkdown: str = ''
    recruitmentUrl: str | None = None
    publicationArchiveUrl: str | None = None
    publicEmail: Short | None = None
    contactName: Short = ''
    links: list[Link] = Field(default_factory=list, max_length=30)
    logoAssetId: Id | None = None
    wordmarkAssetId: Id | None = None


class BrandAsset(Model):
    """固定品牌圖只需網址與替代文字；相簿和個人照片由 SQLite 提供。"""
    id: Id
    url: str
    alt: Short = ''


class Opportunity(Model):
    """保留既有聯絡頁用到的固定招募文字，不新增招募 API 或資料表。"""
    title: Short
    bodyMarkdown: str = ''
    requirements: list[Short] = Field(default_factory=list, max_length=100)
    applicationChecklist: list[Short] = Field(default_factory=list, max_length=100)
    applicationUrl: str | None = None
    guidanceUrl: str | None = None
    availability: Short = 'open'
    deadlineAt: Short | None = None


class Site(Model):
    """公開 site.json 的完整形狀；未知欄位一律拒絕，防止整庫快照誤發布。"""
    siteSettings: SiteSettings
    assets: list[BrandAsset] = Field(default_factory=list, max_length=2)
    opportunities: list[Opportunity] = Field(default_factory=list, max_length=100)


def load_site(root):
    """有界讀取固定設定，不能追隨符號連結或夾帶不明欄位。"""
    path = checked_file(root, 'site.json')
    with path.open('rb') as handle:
        raw = handle.read(MAX_SITE_FILE + 1)
    if len(raw) > MAX_SITE_FILE:
        raise ValueError('site.json 容量超限')
    return Site.model_validate(json_loads(raw.decode())).model_dump()


def check_site(root, site, rows):
    """同時驗證固定設定與 SQLite／CSV 的本地檔案關聯，外站不下載。"""
    site = Site.model_validate(site).model_dump()
    if len(json.dumps(site, ensure_ascii=False).encode()) > MAX_SITE_FILE:
        raise ValueError('site.json 容量超限')
    ids = {asset['id'] for asset in site['assets']}
    if len(ids) != len(site['assets']) or any(site['siteSettings'][key] not in ids | {None}
                                           for key in ('logoAssetId', 'wordmarkAssetId')):
        raise ValueError('固定品牌圖關聯無效')
    links = [a['url'] for a in site['assets']] + content_links(rows)
    for value in links:
        relative = local_asset(value)
        if relative is not None:
            if Path(relative).suffix.lower() not in STATIC_SUFFIXES:
                raise ValueError('不支援的公開檔案型別')
            checked_file(root, relative)


def content_links(state):
    """SQLite、首次轉換及缺檔檢查共用三類內容的檔案引用規則。"""
    return ([a['url'] for a in state['assets']]
            + [p['portrait']['url'] for p in state['people'] if p['portrait']]
            + [p['pdfPath'] for p in state['publications'] if p['pdfPath']])


def copy_public(root, target, files):
    """複製已驗證清單，串流限額、拒絕 symlink，並檢查來源沒有在複製中改動。"""
    target.mkdir(mode=0o700)
    (target / 'assets').mkdir(mode=0o700)
    total = 0
    for path, relative in files:
        checked_file(root, relative)
        output = target / relative
        output.parent.mkdir(parents=True, exist_ok=True)
        with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW), 'rb') as source, output.open('xb') as dest:
            before = os.fstat(source.fileno())
            info = copy_and_hash(source, dest, limit=min(MAX_STATIC_FILE, MAX_STATIC_TOTAL - total))
            after = os.fstat(source.fileno())
            if (before.st_size, before.st_mtime_ns, before.st_ino) != (after.st_size, after.st_mtime_ns, after.st_ino):
                raise ValueError('複製期間來源被修改')
            total += info['size']
            dest.flush(); os.fsync(dest.fileno())


def install_public(store, source, apply=False):
    """首次檔案安裝：先匯入 CSV 再驗引用；拒絕覆寫執行目錄，所有服務須停止。"""
    with store.maintenance():
        target = store.root / 'public'
        if target.exists() or target.is_symlink() or source.is_symlink():
            raise ValueError('只允許首次安裝到不存在的 public 目錄')
        root = source.resolve(strict=True)
        check_site(root, load_site(root), store.read())
        files = static_files(root)
        if apply:
            pending = store.root / ('.public-' + uuid.uuid4().hex)
            try:
                copy_public(root, pending, files)
                check_site(pending, load_site(pending), store.read())
                for folder, _, _ in os.walk(pending, topdown=False):
                    sync_dir(Path(folder))
                pending.rename(target)
                sync_dir(store.root)
            finally:
                if pending.exists():
                    shutil.rmtree(pending)
        return {'files': len(files), 'applied': apply}


def copy_and_hash(source, target=None, *, limit):
    """備份、驗證與恢復共用串流大小限制及SHA256；驗證時不提供寫入目標。"""
    digest, size = hashlib.sha256(), 0
    while chunk := source.read(1024 * 1024):
        size += len(chunk)
        if size > limit:
            raise ValueError('靜態檔案超限')
        digest.update(chunk)
        if target is not None:
            target.write(chunk)
    return {'size': size, 'sha256': digest.hexdigest()}


def local_asset(value):
    """URL只轉成本地相對路徑，不連外、不容許路徑穿越或非assets目錄。"""
    parsed = urllib.parse.urlsplit(urllib.parse.unquote(value))
    if parsed.scheme or parsed.netloc:
        return None
    path = parsed.path.removeprefix('./').removeprefix('/')
    parts = path.split('/')
    if not path.startswith('assets/') or any(p in ('', '.', '..') for p in parts) or '\\' in path:
        raise ValueError('資產路徑無效')
    return path


def checked_file(root, relative):
    """每一層均不得為符號連結；只接受指定根目錄內的普通檔案。"""
    path = root
    # Python3.14的is_file／is_symlink會吞掉權限錯誤；lstat保留EACCES供CLI正確分類。
    try:
        for part in PurePosixPath(relative).parts:
            path = path / part
            if stat.S_ISLNK(path.lstat().st_mode):
                raise ValueError('不接受符號連結')
        if not stat.S_ISREG(path.lstat().st_mode):
            raise ValueError('檔案不存在或不是普通檔案')
    except (FileNotFoundError, NotADirectoryError):
        raise ValueError('檔案不存在或不是普通檔案') from None
    return path


def check_files(store, web_root):
    """離線查SQLite中的本地圖片／PDF；外部URL只列為未驗證，不發送請求。"""
    root = web_root.resolve(strict=True)
    state = store.read()
    links = content_links(state)
    missing, external, checked = [], 0, 0
    if (root / 'site.json').exists() or (root / 'site.json').is_symlink():
        site = load_site(root)
        links += [a['url'] for a in site['assets']]
        ids = {a['id'] for a in site['assets']}
        if any(site['siteSettings'][key] not in ids | {None} for key in ('logoAssetId', 'wordmarkAssetId')):
            raise ValueError('固定品牌圖關聯無效')
    else:
        missing.append('site.json')
    for value in dict.fromkeys(links):
        relative = local_asset(value)
        if relative is None:
            external += 1
            continue
        try:
            checked_file(root, relative)
        except ValueError:
            missing.append(relative)
        checked += 1
    return {'ok': not missing, 'checked': checked, 'externalUnchecked': external, 'missing': missing}


def static_files(web_root, *, include_site=True):
    """只列 assets 與白名單 site.json；原始 data、程式及私人參考不入備份。"""
    root = web_root.resolve(strict=True)
    folder = root / 'assets'
    if folder.is_symlink() or not folder.is_dir():
        raise ValueError('缺少普通assets目錄')
    files, total = [], 0
    for current, directories, names in os.walk(folder, followlinks=False):
        for name in directories + names:
            if (Path(current) / name).is_symlink():
                raise ValueError('assets內不接受符號連結')
        for name in sorted(names):
            path = Path(current) / name
            if not path.is_file():
                raise ValueError('只允許普通靜態檔案')
            if path.suffix.lower() not in STATIC_SUFFIXES:
                raise ValueError('assets 只接受圖片及 PDF')
            size = path.stat().st_size
            total += size
            if size > MAX_STATIC_FILE or total > MAX_STATIC_TOTAL or len(files) >= MAX_STATIC_FILES:
                raise ValueError('靜態備份容量超限')
            files.append((path, path.relative_to(root).as_posix()))
    if include_site:
        site = load_site(root)
        check_site(root, site, {'assets': [], 'people': [], 'publications': []})
        if len(files) >= MAX_STATIC_FILES or total + (root / 'site.json').stat().st_size > MAX_STATIC_TOTAL:
            raise ValueError('靜態備份容量超限')
        files.append((root / 'site.json', 'site.json'))
    return sorted(files, key=lambda item: item[1])


def backup_static(store, web_root, output, version, database_backup):
    """另存公開靜態檔，manifest記錄DB備份指紋及逐檔版本；要求停止管理及檔案更新。"""
    with store.maintenance(), backup_job(store, 'static', version) as result:
        if output.resolve().is_relative_to((web_root / 'assets').resolve()):
            raise ValueError('備份不得寫入被備份的assets目錄')
        db = backup_state(store).get('database', {})
        if not db.get('ok') or db.get('version') != version or db.get('sha256') != file_hash(database_backup):
            raise ValueError('需提供同版本的成功資料庫備份')
        snapshot = store.read()
        if content_hash(snapshot) != db.get('contentHash'):
            raise ValueError('內容已改變，請重新備份資料庫再備份靜態檔')
        report = check_files(store, web_root)
        if not report['ok']:
            raise ValueError('存在缺失的本地資產，請先執行check-files')
        manifest = {'format': 1, 'version': version, 'createdAt': now(), 'databaseSha256': db['sha256'],
                    'contentHash': db['contentHash'], 'externalUnchecked': report['externalUnchecked'], 'files': []}
        pending = output.with_name('.' + output.name + '.' + uuid.uuid4().hex)
        try:
            fd = os.open(pending, os.O_CREAT | os.O_EXCL | os.O_RDWR | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, 'w+b') as handle:
                total = 0
                with zipfile.ZipFile(handle, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
                    for path, relative in static_files(web_root):
                        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
                        with os.fdopen(descriptor, 'rb') as source, archive.open(relative, 'w', force_zip64=True) as target:
                            before = os.fstat(source.fileno())
                            info = copy_and_hash(source, target, limit=min(MAX_STATIC_FILE, MAX_STATIC_TOTAL - total))
                            total += info['size']
                            after = os.fstat(source.fileno())
                            if (before.st_size, before.st_mtime_ns, before.st_ino) != (after.st_size, after.st_mtime_ns, after.st_ino):
                                raise ValueError('靜態檔正在更新，請暫停修改後重試')
                        manifest['files'].append({'path': relative, **info})
                    encoded = json.dumps(manifest, ensure_ascii=False).encode()
                    if len(encoded) > 4 * 1024 * 1024:
                        raise ValueError('manifest過大')
                    archive.writestr('manifest.json', encoded)
                handle.flush(); os.fsync(handle.fileno())
            # link的目的地必須不存在；不覆寫既有備份。
            os.link(pending, output); sync_dir(output.parent)
            result.update(sha256=file_hash(output), databaseSha256=db['sha256'], contentHash=db['contentHash'])
        finally:
            pending.unlink(missing_ok=True)
    return manifest


def verify_static(archive_path, database_backup):
    """先驗所有路徑、大小、CRC及SHA256，再允許恢復到全新目錄。"""
    with zipfile.ZipFile(archive_path) as archive:
        entries = archive.infolist()
        names = [entry.filename for entry in entries]
        if len(names) != len(set(names)) or len(names) > MAX_STATIC_FILES + 1:
            raise ValueError('靜態備份檔案清單無效')
        if 'site.json' not in names:
            raise ValueError('靜態備份缺少固定設定')
        if archive.getinfo('manifest.json').file_size > 4 * 1024 * 1024:
            raise ValueError('manifest過大')
        manifest = json_loads(archive.read('manifest.json').decode())
        if manifest['format'] != 1 or manifest['databaseSha256'] != file_hash(database_backup):
            raise ValueError('資料庫備份與靜態版本不相符')
        files = manifest['files']
        if len(files) != len({item['path'] for item in files}) or set(names) != {'manifest.json', *(item['path'] for item in files)}:
            raise ValueError('manifest檔案不完整或重複')
        total = 0
        for item in files:
            relative = item['path']
            if relative != 'site.json' and (local_asset('/' + relative) != relative or Path(relative).suffix.lower() not in STATIC_SUFFIXES):
                raise ValueError('備份路徑無效')
            info = archive.getinfo(relative)
            if info.file_size > MAX_STATIC_FILE or stat.S_ISLNK(info.external_attr >> 16):
                raise ValueError('備份檔案型別／容量無效')
            with archive.open(relative) as source:
                actual = copy_and_hash(source, limit=min(MAX_STATIC_FILE, MAX_STATIC_TOTAL - total))
            total += actual['size']
            if actual != {key: item[key] for key in actual}:
                raise ValueError('靜態備份指紋不符')
            if relative == 'site.json':
                if actual['size'] > MAX_SITE_FILE:
                    raise ValueError('site.json 容量超限')
                Site.model_validate(json_loads(archive.read(relative).decode()))
    return manifest


def restore_static(archive_path, database_backup, target, apply=False):
    """預覽為預設；套用只建立新目錄，不覆寫網站或資料庫。"""
    manifest = verify_static(archive_path, database_backup)
    if apply:
        target.mkdir(mode=0o700, parents=False, exist_ok=False)
        try:
            with zipfile.ZipFile(archive_path) as archive:
                for item in manifest['files']:
                    path = target / item['path']; path.parent.mkdir(parents=True, exist_ok=True)
                    # 不採用extractall，目的地一律來自已驗證的相對路徑。
                    with archive.open(item['path']) as source, open(path, 'xb') as destination:
                        # 第二次讀取仍核對指紋，避免預覽後封存檔被替換。
                        actual = copy_and_hash(source, destination, limit=item['size'])
                        if actual != {key: item[key] for key in actual}:
                            raise ValueError('恢復時檔案指紋改變')
                        destination.flush(); os.fsync(destination.fileno())
            for folder, _, _ in os.walk(target, topdown=False):
                sync_dir(Path(folder))
            write_new(target / 'manifest.json', json.dumps(manifest, ensure_ascii=False).encode())
            sync_dir(target)
        except Exception:
            shutil.rmtree(target)
            raise
    return {'version': manifest['version'], 'files': len(manifest['files']), 'applied': apply}


def container_probe(service, endpoint):
    """維護容器直連固定內網端點；不用主機 Python、Docker CLI 或 socket。"""
    if service not in ('public-api', 'admin-api') or endpoint not in ('healthz', 'readyz'):
        raise ValueError('不支援的監測端點')
    # 固定內網GET探測不需要公開域名、登入Cookie或Origin。
    request = urllib.request.Request(f'http://{service}:8000/{endpoint}')
    try:
        # 禁止沿用 HTTP_PROXY，避免把內部探測送到外部代理。
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(request, timeout=5) as response:
            return response.status
    except urllib.error.HTTPError as exc:
        status = exc.code
        exc.close()
        return status
    except (ValueError, OSError):
        return 0


def monitor(store, probe, min_free=256*1024*1024, max_backup_hours=48, threshold=3):
    """一次採樣；連續不可用計數跨程序保存，告警寫JSON/stderr並由CLI退出碼通知排程。"""
    private = store.admin_path.parent
    with exclusive_lock(private / 'monitor.lock', create=True):
        state_path = private / 'monitor-state.json'
        alerts, services, counters = [], {}, {}
        try:
            previous = json_loads(read_file(state_path)) if state_path.exists() else {}
            old_counts = previous.get('counters', {})
            if not isinstance(old_counts, dict) or any(type(n) is not int or n < 0 for n in old_counts.values()):
                raise ValueError('invalid counters')
        except (ValueError, AttributeError, OSError):
            previous = {}
            alerts.append({'code': 'MONITOR_STATE_INVALID'})
        for service in ('public-api', 'admin-api'):
            live, ready = probe(service, 'healthz'), probe(service, 'readyz')
            count = 0 if ready == 200 else int(previous.get('counters', {}).get(service, 0)) + 1
            counters[service] = count
            services[service] = {'live': live, 'ready': ready, 'consecutiveUnavailable': count}
            if live != 200: alerts.append({'code': 'PROCESS_UNAVAILABLE', 'service': service})
            if count >= threshold:
                alerts.append({'code': 'CONTENT_UNAVAILABLE', 'service': service, 'status': ready, 'consecutive': count})
        free = min(shutil.disk_usage(path.parent).free for path in (store.content_path, store.admin_path))
        if free < min_free: alerts.append({'code': 'LOW_DISK', 'freeBytes': free})
        try:
            jobs = backup_state(store)
            for kind in ('database', 'static'):
                item = jobs.get(kind, {})
                if not item.get('ok'): alerts.append({'code': 'BACKUP_FAILED_OR_MISSING', 'kind': kind})
                last = item.get('lastSuccessAt')
                if not last or (datetime.now(timezone.utc) - datetime.fromisoformat(last)).total_seconds() > max_backup_hours * 3600:
                    alerts.append({'code': 'BACKUP_OVERDUE', 'kind': kind})
            # 新DB備份若尚未有同版本靜態副本，兩個工作各自成功也不能算完整配對。
            database, static = jobs.get('database', {}), jobs.get('static', {})
            if database.get('ok') and static.get('ok') and (
                database.get('version') != static.get('version') or
                database.get('sha256') != static.get('databaseSha256')
            ):
                alerts.append({'code': 'BACKUP_VERSION_MISMATCH'})
        except (ValueError, TypeError, AttributeError, OSError):
            alerts.append({'code': 'BACKUP_STATUS_INVALID'})
        report = {'at': now(), 'ok': not alerts, 'services': services, 'freeBytes': free, 'alerts': alerts, 'counters': counters}
        atomic_json(state_path, report)
        for alert in alerts:
            print('ALERT ' + json.dumps(alert, ensure_ascii=False), file=sys.stderr)
        return report


def main():
    """明確指定離線操作；不讀取lab.json，錯誤只回類型避免輸出秘密。"""
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    install = sub.add_parser('install-public')
    install.add_argument('source', type=Path); install.add_argument('--apply', action='store_true')
    check = sub.add_parser('check-files'); check.add_argument('web_root', type=Path)
    bak = sub.add_parser('backup-static')
    bak.add_argument('web_root', type=Path); bak.add_argument('output', type=Path)
    bak.add_argument('--version', required=True); bak.add_argument('--database-backup', required=True, type=Path)
    restore = sub.add_parser('restore-static')
    restore.add_argument('archive', type=Path); restore.add_argument('--database-backup', required=True, type=Path)
    restore.add_argument('--target', required=True, type=Path); restore.add_argument('--apply', action='store_true')
    mon = sub.add_parser('monitor')
    mon.add_argument('--min-free-mib', type=int, default=256); mon.add_argument('--max-backup-hours', type=int, default=48)
    mon.add_argument('--threshold', type=int, default=3)
    mon.add_argument('--interval', type=int, default=0, help='0只檢查一次；正數為容器內重複檢查的間隔秒數')
    args = parser.parse_args()
    config = Config.load()
    store = Store(config.storage)
    try:
        if args.command == 'install-public': result = install_public(store, args.source, args.apply)
        elif args.command == 'check-files': result = check_files(store, args.web_root)
        elif args.command == 'backup-static':
            manifest = backup_static(store, args.web_root, args.output, args.version, args.database_backup)
            result = {'ok': True, 'version': manifest['version'], 'files': len(manifest['files'])}
        elif args.command == 'restore-static': result = restore_static(args.archive, args.database_backup, args.target, args.apply)
        else:
            if min(args.min_free_mib, args.max_backup_hours, args.threshold) <= 0 or args.interval < 0:
                raise ValueError('監測門檻須為正數，間隔不可為負數')
            while True:
                # 告警不能終止後續採樣；一次性呼叫則仍以退出碼回報失敗。
                result = monitor(store, container_probe,
                                 args.min_free_mib * 1024 * 1024, args.max_backup_hours, args.threshold)
                print(json.dumps(result, ensure_ascii=False), flush=True)
                if not args.interval:
                    return 0 if result['ok'] else 1
                time.sleep(args.interval)
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result.get('ok', True) else 1
    except Exception as exc:
        print('ALERT maintenance_failed type=' + type(exc).__name__, file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
