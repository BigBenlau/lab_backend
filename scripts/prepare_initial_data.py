"""將指定前端的首次內容轉成 CSV＋公開檔案；不開資料庫、不自動匯入。"""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lab_backend.models import FILES, MODELS, empty_state, new_metadata, validate_state
from lab_backend.store import encode_csv, json_loads, read_file, sync_dir, validate_capacity, write_new
from scripts.maintenance import (Site, SiteSettings, Opportunity, checked_file, check_site,
                                 copy_public, static_files, MAX_STATIC_FILES, MAX_STATIC_TOTAL)


def convert(snapshot):
    """只轉換已確認欄位；保留作者 ID、來源連結和入庫順序，不依姓名猜關聯。"""
    def public(row):
        # 舊來源的狀態只在首次轉換使用，執行庫沒有草稿或發布功能。
        return (row['publishState'] == 'published' and not row['isDemo']
                and row['publishedAt'] and datetime.fromisoformat(row['publishedAt']) <= datetime.now(timezone.utc))

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


def prepare(source, output, apply=False):
    """預覽先驗型別、關聯、容量及缺檔；套用只建立新 bundle，失敗不留半份。"""
    if source.is_symlink() or not source.is_dir() or output.exists() or output.is_symlink():
        raise ValueError('來源須為普通目錄，輸出必須不存在')
    root = source.resolve(strict=True)
    if output.resolve().is_relative_to(root):
        raise ValueError('輸出不可放在初始化來源內')
    # 只讀使用者指定的前端來源，絕不搜尋根目錄私人 lab.json。
    rows, site = convert(json_loads(read_file(checked_file(root, 'data/lab.json'))))
    files = static_files(root, include_site=False)
    check_site(root, site, rows)
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
    """容器內明確執行；錯誤只輸出類型，避免把私人來源值寫入日誌。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    try:
        print(json.dumps(prepare(args.source, args.output, args.apply)))
        return 0
    except Exception as exc:
        print('initial_data_failed type=' + type(exc).__name__, file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
