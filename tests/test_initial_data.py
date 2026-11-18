"""合成首次來源→CSV→SQLite→公開檔案；不讀真正初始化資料，不啟動服務。"""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from lab_backend.cli import backup, import_csv
from lab_backend.store import Store, StoreError
from scripts.maintenance import (backup_static, check_files, install_public,
                                 load_site, restore_static)
from scripts.prepare_initial_data import convert, prepare


def seed():
    """刻意以較舊年份的新論文在前，檢查轉換不偷換成按年份排序。"""
    status = {'publishState': 'published', 'isDemo': False, 'publishedAt': '2020-01-01T00:00:00Z'}
    return {
        'siteSettings': {'name': 'Synthetic Lab', 'logoAssetId': 'logo', 'wordmarkAssetId': 'logo',
                         'heroPhotoAssetIds': ['group'], 'privateIgnored': 'never-published'},
        'assets': [{'id': key, 'url': '/assets/' + key + '.svg', 'mimeType': 'image/svg+xml',
                    'alt': key, 'caption': None, 'credit': ''} for key in ('logo', 'group', 'portrait')],
        'people': [{**status, 'id': 'person', 'name': 'Ada Example', 'familyName': 'Example',
                    'preferredName': 'Amy', 'title': '', 'portraitAssetId': 'portrait',
                    'memberships': [{'role': 'phd_student', 'isCurrent': False}], 'state': 'alumni',
                    'alumniSince': '2025-06', 'bioMarkdown': 'Synthetic bio', 'interests': [],
                    'publicEmail': None, 'links': []}],
        'publications': [{**status, 'id': key, 'title': key, 'year': year, 'type': 'journal',
                          'venue': {'name': '', 'shortName': ''}, 'pdfUrl': None, 'pdfAssetId': None,
                          'pageUrl': '#publications/' + key, 'sourceUrl': 'https://example.invalid/' + key,
                          'authors': [{'personId': 'person', 'name': 'Exact Signature'},
                                      {'personId': None, 'name': 'External Author'}]}
                         for key, year in [('new', 2020), ('old', 2026)]],
        'opportunities': [{**status, 'title': 'Synthetic position', 'bodyMarkdown': 'Apply', 'requirements': []}],
    }


class InitialDataTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='lab-initial-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / 'seed'
        (self.source / 'data').mkdir(parents=True)
        (self.source / 'assets').mkdir()
        self.snapshot = seed()
        self.save_source()
        for asset in self.snapshot['assets']:
            (self.source / asset['url'].lstrip('/')).write_text('<svg/>')
        self.output = self.root / 'bundle'

    def save_source(self):
        """只在暫存目錄建立同名合成 JSON。"""
        (self.source / 'data/lab.json').write_text(json.dumps(self.snapshot))

    def test_preview_apply_import_and_runtime_is_independent_of_seed(self):
        self.assertFalse(prepare(self.source, self.output)['applied'])
        self.assertFalse(self.output.exists())
        report = prepare(self.source, self.output, True)
        self.assertEqual(report['counts'], {'assets': 1, 'people': 1, 'publications': 2})
        self.assertEqual({p.name for p in self.output.iterdir()}, {'content', 'public'})
        site = load_site(self.output / 'public')
        self.assertEqual(set(site), {'siteSettings', 'assets', 'opportunities'})
        self.assertNotIn('privateIgnored', site['siteSettings'])
        store = Store(self.root / 'store'); store.initialize()
        import_csv(store, self.output / 'content')
        self.assertFalse(store.read()['people'])
        import_csv(store, self.output / 'content', True)
        rows = store.read()
        self.assertEqual([row['id'] for row in rows['publications']], ['old', 'new'])
        self.assertEqual(rows['publications'][1]['sourceUrl'], 'https://example.invalid/new')
        self.assertEqual(rows['people'][0]['englishName'], 'Amy')
        self.assertEqual(rows['people'][0]['alumniSince'], '2025-06')
        self.assertFalse(install_public(store, self.output / 'public')['applied'])
        self.assertFalse((store.root / 'public').exists())
        install_public(store, self.output / 'public', True)
        self.assertTrue(check_files(store, store.root / 'public')['ok'])
        (self.source / 'assets/logo.svg').write_text('source changed')
        self.assertEqual((store.root / 'public/assets/logo.svg').read_text(), '<svg/>')
        self.assertFalse((store.root / 'public/data/lab.json').exists())
        with self.assertRaises(ValueError): prepare(self.source, self.output, True)
        with self.assertRaises(ValueError): install_public(store, self.output / 'public', True)

    def test_bad_relations_capacity_dates_and_missing_files_leave_no_bundle(self):
        cases = []
        raw = seed(); raw['publications'][0]['authors'][0]['personId'] = 'unknown'; cases.append(raw)
        raw = seed(); raw['people'][0]['memberships'][0]['isCurrent'] = True; raw['people'][0]['state'] = 'member'; cases.append(raw)
        raw = seed(); raw['people'][0]['bioMarkdown'] = 'a' * 50001; cases.append(raw)
        raw = seed(); raw['assets'][1]['url'] = '/assets/missing.svg'; cases.append(raw)
        raw = seed(); raw['assets'][1]['url'] = '/assets/../secret.svg'; cases.append(raw)
        for raw in cases:
            self.snapshot = raw; self.save_source()
            for apply in (False, True):
                with self.assertRaises(ValueError): prepare(self.source, self.output, apply)
                self.assertFalse(self.output.exists())
        with patch('lab_backend.store.MAX_REQUEST', 20):
            with self.assertRaises(ValueError): convert(seed())

    def test_unknown_or_symlink_assets_and_private_site_fields_rejected(self):
        unsafe = self.source / 'assets/unsafe.html'; unsafe.write_text('private')
        with self.assertRaises(ValueError): prepare(self.source, self.output, True)
        unsafe.unlink()
        link = self.source / 'assets/link.svg'; link.symlink_to(self.source / 'assets/logo.svg')
        with self.assertRaises(ValueError): prepare(self.source, self.output, True)
        link.unlink()
        prepare(self.source, self.output, True)
        path = self.output / 'public/site.json'
        site = json.loads(path.read_text()); site['people'] = seed()['people']; path.write_text(json.dumps(site))
        store = Store(self.root / 'store'); store.initialize()
        with self.assertRaises(ValueError): install_public(store, self.output / 'public', True)
        self.assertFalse((store.root / 'public').exists())

    def test_copy_failure_rolls_back_bundle_and_runtime_directory(self):
        with patch('scripts.maintenance.copy_and_hash', side_effect=OSError):
            with self.assertRaises(OSError): prepare(self.source, self.output, True)
        self.assertFalse(self.output.exists())
        self.assertFalse(list(self.root.glob('.initial-*')))
        prepare(self.source, self.output, True)
        store = Store(self.root / 'store'); store.initialize()
        with patch('scripts.maintenance.copy_and_hash', side_effect=OSError):
            with self.assertRaises(StoreError): install_public(store, self.output / 'public', True)
        self.assertFalse((store.root / 'public').exists())
        self.assertFalse(list(store.root.glob('.public-*')))

    def test_site_and_assets_are_backed_up_with_database_fingerprint(self):
        prepare(self.source, self.output, True)
        store = Store(self.root / 'store'); store.initialize()
        import_csv(store, self.output / 'content', True)
        install_public(store, self.output / 'public', True)
        database, static = self.root / 'db.enc', self.root / 'public.zip'
        with patch('lab_backend.cli.password_prompt', return_value='synthetic-backup-password'):
            backup(store, database, version='initial')
        manifest = backup_static(store, store.root / 'public', static, 'initial', database)
        self.assertIn('site.json', [entry['path'] for entry in manifest['files']])
        restored = self.root / 'restored'
        restore_static(static, database, restored, True)
        self.assertEqual(load_site(restored), load_site(store.root / 'public'))
        self.assertTrue(check_files(store, restored)['ok'])
        (restored / 'site.json').unlink()
        self.assertIn('site.json', check_files(store, restored)['missing'])
