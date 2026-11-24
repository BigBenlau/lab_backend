"""將指定前端的首次內容轉成 CSV＋公開檔案；不開資料庫、不自動匯入。"""
import argparse
from datetime import datetime, timezone
import errno
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lab_backend.models import FILES, MODELS, empty_state, new_metadata, validate_state
from lab_backend.store import encode_csv, json_loads, read_file, sync_dir, validate_capacity, write_new
from scripts.maintenance import (Site, SiteSettings, Opportunity, checked_file, check_site,
                                 content_links, copy_public, local_asset, site_links, static_files, MAX_STATIC_FILES, MAX_STATIC_TOTAL)


# 只允許程式內固定訊息映射成提示；不能直接輸出例外字串，當中可能含姓名、ID或JSON。
SAFE_FAILURES = {
    '來源須為普通目錄': ('source_directory_invalid', '確認前端dist已掛到/seed，容器UID10001可讀，且不是符號連結。'),
    '輸出已存在': ('output_exists', '初始化bundle已存在，拒絕覆寫；先核對先前是否已成功，勿刪volume或重跑init。'),
    '輸出不可放在初始化來源內': ('output_inside_source', '輸出需在私有資料目錄，不可放入唯讀的初始化來源。'),
    '初始化JSON不存在或不是安全普通檔案': ('source_json_unavailable', '另行提供public_frontend/dist/data/lab.json；確認檔案可讀且路徑不含符號連結。'),
    '來源資產 ID 重複': ('duplicate_asset_id', '在本機核對來源assets，ID不可重複。'),
    '來源姓／名需先人工確認': ('person_name_mismatch', '核對來源people的name與familyName；目前轉換要求姓名最後一部分為familyName。'),
    '來源身份不一致': ('person_membership_mismatch', '核對people.state與memberships.isCurrent是否一致，不自動更改身份。'),
    '來源發布時間無效': ('published_at_invalid', '來源publishedAt須為含時區的ISO日期時間。'),
    '來源有未轉換的外部論文／PDF連結': ('publication_link_unconverted', '核對論文pageUrl及pdfUrl；目前需站內論文路由，PDF透過pdfAssetId引用。'),
    '缺少普通assets目錄': ('assets_directory_invalid', '另行提供前端dist/assets，確認為容器可讀的普通目錄。'),
    'assets 只接受圖片及 PDF': ('asset_type_unsupported', 'assets只可包含svg/png/jpg/jpeg/webp/pdf；檢查是否夾帶.DS_Store、文字或其他檔案。'),
    '不支援的公開檔案型別': ('asset_type_unsupported', '核對內容引用的檔案，只支援svg/png/jpg/jpeg/webp/pdf。'),
    '檔案不存在或不是普通檔案': ('asset_file_unavailable', '核對來源引用的圖片／PDF是否已完整放入dist/assets，且UID10001可讀。'),
    '只允許普通靜態檔案': ('asset_file_unavailable', 'assets內只接受普通檔案。'),
    '不接受符號連結': ('symlink_not_allowed', '來源路徑不接受符號連結；提供實體檔案。'),
    'assets內不接受符號連結': ('symlink_not_allowed', 'assets內不接受符號連結；提供實體檔案。'),
    '資產路徑無效': ('asset_path_invalid', '核對資產URL，站內檔案必須位於assets，不能包含路徑穿越。'),
    '固定品牌圖關聯無效': ('brand_reference_invalid', '核對logoAssetId及wordmarkAssetId是否引用來源assets中的項目。'),
    '重複 JSON 欄位': ('json_duplicate_key', '來源JSON包含重複物件欄位，請在本機修正。'),
    '不允許非有限數字': ('json_nonfinite_number', '來源JSON不可含NaN或Infinity。'),
    '檔案超限': ('source_json_too_large', '初始化JSON超過32MiB限制。'),
    'site.json 容量超限': ('site_too_large', '固定頁面設定超過1MiB限制。'),
    '靜態備份容量超限': ('assets_too_large', '檢查靜態檔案的單檔、總容量及數量限制。'),
    '靜態檔案超限': ('assets_too_large', '檢查靜態檔案的單檔及總容量限制。'),
    '含固定設定的公開檔案總容量超限': ('assets_too_large', '圖片／PDF加上固定設定超過公開檔案容量或數量限制。'),
    '複製期間來源被修改': ('source_changed', '套用期間來源檔案有變動，請停止修改後重新預覽。'),
}


def failure_details(exc):
    """回傳可安全分享的代碼與固定提示；不輸出原始錯誤、輸入值或私有路徑。"""
    if isinstance(exc, ValidationError):
        return 'content_validation_failed', '內容欄位未通過型別／長度／URL／校友日期驗證，請依資料契約核對來源。'
    if isinstance(exc, json.JSONDecodeError):
        return 'json_syntax_invalid', f'來源JSON語法錯誤，位置：第{exc.lineno}行、第{exc.colno}欄。'
    if isinstance(exc, UnicodeError):
        return 'source_encoding_invalid', '來源JSON必須使用UTF-8編碼。'
    if isinstance(exc, ValueError):
        message = str(exc)
        if message in SAFE_FAILURES:
            return SAFE_FAILURES[message]
        # 這些共用驗證可能在訊息中帶上實際ID；只輸出固定分類，不轉印字串。
        if message.startswith('publications 關聯 people/') and message.endswith('不存在，不能刪除被引用資料'):
            return 'author_reference_invalid', 'authors.personId必須引用本次匯入的people；核對成員是否被來源的發布狀態過濾。'
        for resource in MODELS:
            if message == f'{resource} ID 重複':
                return 'duplicate_content_id', '核對同一類內容的ID，不可重複。'
            if message in (f'{resource} 超過資料大小上限，未提交；請先減少內容',
                           f'{resource} 超過交換檔大小上限，未提交',
                           f'{resource} 單筆過大，將無法在管理頁編輯，未提交'):
                return 'content_too_large', '核對每表／CSV的32MiB及單筆業務JSON的252KiB限制。'
    if isinstance(exc, KeyError):
        return 'source_key_or_reference_missing', '來源缺少舊格式必要欄位，或引用的資產ID不存在；不輸出該欄位／ID值。'
    if isinstance(exc, (TypeError, AttributeError)):
        return 'source_shape_invalid', '來源的物件、陣列或欄位型別不符合首次匯入格式。'
    if isinstance(exc, PermissionError):
        return 'permission_denied', '確認UID10001可讀來源檔案、可進入dist及data/assets各層目錄，並可寫/data/private；只看JSON的644不足以確認可讀，不要使用chmod 777。'
    if isinstance(exc, FileExistsError):
        return 'output_exists', SAFE_FAILURES['輸出已存在'][1]
    if isinstance(exc, OSError):
        if exc.errno == errno.ENOSPC:
            return 'disk_full', '檢查Docker資料磁碟的可用空間；不要刪除volume或journal排錯。'
        return 'filesystem_error', '檢查來源／輸出掛載、檔案權限及磁碟狀態。'
    return 'unclassified_error', '尚未分類的轉換錯誤；提供type與code協助排查，不要貼出私人JSON。'


def convert(snapshot):
    """只轉換已確認欄位；保留作者 ID、來源連結和入庫順序，不依姓名猜關聯。"""
    def public(row):
        # 舊來源的狀態只在首次轉換使用，執行庫沒有草稿或發布功能。
        if row['publishState'] != 'published' or row['isDemo'] or not row['publishedAt']:
            return False
        try:
            published_at = datetime.fromisoformat(row['publishedAt'])
            if published_at.tzinfo is None:
                raise ValueError
        except (ValueError, TypeError):
            raise ValueError('來源發布時間無效') from None
        return published_at <= datetime.now(timezone.utc)

    def photo(asset):
        """照片直接進 portrait，捨棄寬高及舊管理欄位。"""
        return {'url': asset['url'], 'metadata': {
            key: asset[key] for key in ('mimeType', 'alt', 'caption', 'credit')}}

    assets = {row['id']: row for row in snapshot['assets']}
    if len(assets) != len(snapshot['assets']):
        raise ValueError('來源資產 ID 重複')
    settings = snapshot['siteSettings']
    rows = {key: [] for key in MODELS}
    for asset_id in settings['heroPhotoAssetIds']:
        item = photo(assets[asset_id])
        item['metadata']['purpose'] = 'group_photo'
        rows['assets'].append({'id': asset_id, **item})
    for person in filter(public, snapshot['people']):
        full_name = person['name'].split(',', 1)[0].strip()
        given, separator, family = full_name.rpartition(' ')
        if not separator or family.casefold() != person['familyName'].casefold():
            raise ValueError('來源姓／名需先人工確認')
        memberships = [{key: role[key] for key in ('role', 'isCurrent')} for role in person['memberships']]
        if any(role['isCurrent'] for role in memberships) != (person['state'] == 'member'):
            raise ValueError('來源身份不一致')
        rows['people'].append({
            'id': person['id'], 'familyName': person['familyName'], 'givenName': given,
            'englishName': person.get('englishName', person.get('preferredName')),
            'title': person['title'] or '', 'alumniSince': person.get('alumniSince'),
            'portrait': photo(assets[person['portraitAssetId']]) if person['portraitAssetId'] else None,
            'bioMarkdown': person['bioMarkdown'] or '', 'interests': person['interests'],
            'memberships': memberships, 'publicEmail': person['publicEmail'], 'links': person['links'],
        })
    # 公開 API 為入庫倒序，因此 CSV 先放舊項目，維持原首頁順序。
    for paper in filter(public, reversed(snapshot['publications'])):
        if paper.get('pdfUrl') or not paper['pageUrl'].startswith('#publications/'):
            raise ValueError('來源有未轉換的外部論文／PDF連結')
        rows['publications'].append({
            'id': paper['id'], 'title': paper['title'], 'year': paper['year'], 'type': paper['type'],
            'venue': {key: paper['venue'][key] or '' for key in ('name', 'shortName')},
            'authors': [{key: author[key] for key in ('personId', 'name')} for author in paper['authors']],
            'pdfPath': assets[paper['pdfAssetId']]['url'].removeprefix('.') if paper['pdfAssetId'] else None,
            'sourceUrl': paper.get('sourceUrl') or '',
        })
    # 固定設定只保留目前頁面真正使用的欄位，不複製 people、paper 或原始快照。
    fixed = {key: settings[key] for key in SiteSettings.model_fields if key in settings}
    brand_ids = {fixed.get('logoAssetId'), fixed.get('wordmarkAssetId')} - {None}
    site = Site.model_validate({
        'siteSettings': fixed,
        'assets': [{'id': key, 'url': assets[key]['url'], 'alt': assets[key]['alt']} for key in sorted(brand_ids)],
        'opportunities': [{key: row[key] for key in Opportunity.model_fields if key in row}
                          for row in snapshot.get('opportunities', []) if public(row)],
    }).model_dump()
    state = empty_state()
    for resource, entries in rows.items():
        rows[resource] = [MODELS[resource].model_validate(row).model_dump() for row in entries]
        state[resource] = [{**row, **new_metadata('initial')} for row in rows[resource]]
    validate_state(state)
    validate_capacity(state)
    return rows, site


def prepare(source, output, apply=False, include_assets=()):
    """預覽先驗型別、關聯、容量及缺檔；套用只建立新 bundle，失敗不留半份。"""
    if output.exists() or output.is_symlink():
        raise ValueError('輸出已存在')
    if source.is_symlink() or not source.is_dir():
        raise ValueError('來源須為普通目錄')
    root = source.resolve(strict=True)
    if output.resolve().is_relative_to(root):
        raise ValueError('輸出不可放在初始化來源內')
    # 只讀使用者指定的前端來源，絕不搜尋根目錄私人 lab.json。
    try:
        source_json = checked_file(root, 'data/lab.json')
    except ValueError:
        raise ValueError('初始化JSON不存在或不是安全普通檔案') from None
    rows, site = convert(json_loads(read_file(source_json)))
    files = static_files(root, include_site=False)
    check_site(root, site, rows)
    # 只發布頁面實際引用的檔案，額外公開檔須由部署者逐一點名。
    wanted = {relative for url in site_links(site) + content_links(rows) if (relative := local_asset(url))}
    wanted.update(include_assets)
    files = [(path, relative) for path, relative in files if relative in wanted]
    if len(files) != len(wanted):
        raise ValueError('額外資產路徑不存在或無效')
    encoded_site = json.dumps(site, ensure_ascii=False).encode()
    if len(files) + 1 > MAX_STATIC_FILES or sum(path.stat().st_size for path, _ in files) + len(encoded_site) > MAX_STATIC_TOTAL:
        raise ValueError('含固定設定的公開檔案總容量超限')
    result = {'counts': {key: len(entries) for key, entries in rows.items()},
              'files': len(files), 'applied': apply}
    if not apply:
        return result
    output.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    pending = Path(tempfile.mkdtemp(prefix='.initial-', dir=output.parent))
    try:
        (pending / 'content').mkdir(mode=0o700)
        for resource, entries in rows.items():
            write_new(pending / 'content' / FILES[resource], encode_csv(resource, entries, private=False).encode())
        copy_public(root, pending / 'public', files)
        write_new(pending / 'public/site.json', encoded_site)
        check_site(pending / 'public', site, rows)
        for folder, _, _ in os.walk(pending, topdown=False):
            sync_dir(Path(folder))
        # 不覆寫既有結果；運維操作不並行執行。
        if output.exists():
            raise ValueError('輸出已存在')
        pending.rename(output)
        sync_dir(output.parent)
    finally:
        if pending.exists():
            shutil.rmtree(pending)
    return result


def main():
    """容器內明確執行；錯誤輸出安全分類，讓部署可排錯且不洩漏私人來源。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--include-asset', action='append', default=[], help='額外公開的 assets/ 相對路徑，可重複')
    args = parser.parse_args()
    try:
        print(json.dumps(prepare(args.source, args.output, args.apply, args.include_asset)))
        return 0
    except Exception as exc:
        code, hint = failure_details(exc)
        print(f'initial_data_failed type={type(exc).__name__} code={code}', file=sys.stderr)
        print('hint=' + hint, file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
