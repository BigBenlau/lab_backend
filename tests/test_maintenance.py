"""使用合成資料與暫存目錄驗證容量、檔案維護及監測；不執行Docker。"""
import asyncio
import copy
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from fixtures import sample_data, state_from_sample
import test_regressions as helpers
from lab_backend.application import public_app
from lab_backend.cli import backup, backup_state, file_hash, import_csv, restore
from lab_backend.models import MODELS, new_metadata
from lab_backend.store import Store, StoreError, encode_csv, exclusive_lock, json_size, validate_capacity
from scripts.maintenance import backup_static, check_files, monitor, restore_static, verify_static


class MaintenanceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='lab-maintenance-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = Store(self.root / 'storage')
        self.state = state_from_sample(sample_data())
        self.store.initialize(self.state)
        self.web = self.root / 'web'
        (self.web / 'assets').mkdir(parents=True)
        (self.web / 'assets/logo.svg').write_text('<svg/>')

    def test_capacity_rejection_leaves_content_and_audit_editable(self):
        """縮小門檻重現跨界寫入，不配置真實32MiB；失敗後仍能讀取與刪除。"""
        before = self.store.read(include_audit=True)
        baseline = max(sum(json_size(row) for row in rows) for rows in before.values())
        with patch('lab_backend.store.MAX_FILE', baseline + 512):
            def over(state):
                state['people'][0]['bioMarkdown'] = 'X' * 10000
            with self.assertRaises(ValueError): self.store.change(over)
            self.assertEqual(before, self.store.read(include_audit=True))
            # 刪除無關聯的第二位成員仍可提交。
            self.store.change(lambda s: s['people'].pop(1))
            self.assertEqual(len(self.store.read()['people']), 2)
            other = Store(self.root / 'new-storage')
            oversized = copy.deepcopy(before); oversized['people'][0]['bioMarkdown'] = 'X' * 10000
            with self.assertRaises(ValueError): other.initialize(oversized)
            self.assertFalse(other.root.exists())
            with self.assertRaises(ValueError): self.store.replace(oversized)
            self.assertEqual(len(self.store.read()['people']), 2)

    def test_import_preview_apply_and_single_record_share_limits(self):
        batch = self.root / 'batch'; batch.mkdir()
        person = MODELS['people'](id='large', familyName='Test', givenName='Large', bioMarkdown='X' * 12000).model_dump()
        (batch / 'people.csv').write_text(encode_csv('people', [person], private=False))
        before = self.store.read(include_audit=True)
        # CSV大小本身合法，但單筆不能大到管理HTTP表單無法編輯。
        with patch('lab_backend.store.MAX_REQUEST', 8192):
            for apply in (False, True):
                with self.assertRaises(ValueError): import_csv(self.store, batch, apply)
                self.assertEqual(before, self.store.read(include_audit=True))
        # CSV標頭／轉義比JSON更大時，也必須守住恢復讀取邊界。
        asset = {**MODELS['assets'](id='quoted', url='/assets/logo.svg', metadata={'caption': '"' * 5000}).model_dump(), **new_metadata('test')}
        json_bytes = json_size(asset)
        csv_bytes = len(encode_csv('assets', [asset]).encode())
        self.assertGreater(csv_bytes, json_bytes)
        with patch('lab_backend.store.MAX_FILE', (json_bytes + csv_bytes) // 2):
            with self.assertRaises(ValueError): validate_capacity({'assets': [asset]})

    def test_health_and_readiness_are_separate(self):
        async def run():
            app = public_app()
            async with app.router.lifespan_context(app):
                self.assertEqual((await helpers.request(app, '/readyz'))[0], 200)
                with patch.object(app.state.store, 'read', side_effect=StoreError('unavailable')):
                    self.assertEqual((await helpers.request(app, '/healthz'))[0], 200)
                    self.assertEqual((await helpers.request(app, '/readyz'))[0], 503)
            self.assertNotIn('/readyz', app.openapi()['paths'])
            self.assertNotIn('/healthz', app.openapi()['paths'])
        with patch.dict(os.environ, {'LAB_STORAGE': str(self.store.root), 'LAB_DEV': 'true', 'LAB_ADMIN_ORIGIN': 'http://127.0.0.1'}):
            asyncio.run(run())

    def test_offline_file_check_missing_external_and_symlink(self):
        self.assertTrue(check_files(self.store, self.web)['ok'])
        def update(state):
            state['publications'][0]['pdfPath'] = '/assets/papers/missing.pdf'
            state['people'][0]['portrait'] = {'url': 'https://example.invalid/photo.jpg', 'metadata': {'mimeType': 'image/jpeg', 'alt': '', 'caption': None, 'credit': ''}}
        self.store.change(update)
        report = check_files(self.store, self.web)
        self.assertEqual(report['missing'], ['assets/papers/missing.pdf'])
        self.assertEqual(report['externalUnchecked'], 1)
        (self.web / 'assets/papers').mkdir()
        (self.web / 'assets/papers/missing.pdf').symlink_to(self.web / 'assets/logo.svg')
        self.assertFalse(check_files(self.store, self.web)['ok'])

    def paired_backup(self):
        db, static = self.root / 'database.enc', self.root / 'static.zip'
        with patch('lab_backend.cli.password_prompt', return_value='synthetic-backup-password'):
            backup(self.store, db, version='release-1')
        manifest = backup_static(self.store, self.web, static, 'release-1', db)
        return db, static, manifest

    def test_paired_backup_restore_preserves_order_and_manifest(self):
        db, static, manifest = self.paired_backup()
        self.assertEqual(manifest['databaseSha256'], file_hash(db))
        self.assertEqual(manifest['files'][0]['path'], 'assets/logo.svg')
        self.assertEqual(verify_static(static, db), manifest)
        self.assertTrue(monitor(self.store, lambda *_: 200, min_free=1)['ok'])
        status_path = self.store.admin_path.parent / 'backup-status.json'
        status = backup_state(self.store); status['database']['version'] = 'newer'
        status_path.write_text(json.dumps(status))
        with patch('sys.stderr', new_callable=io.StringIO):
            report = monitor(self.store, lambda *_: 200, min_free=1)
        self.assertIn('BACKUP_VERSION_MISMATCH', {a['code'] for a in report['alerts']})
        target = self.root / 'restored-web'
        self.assertFalse(restore_static(static, db, target)['applied'])
        self.assertFalse(target.exists())
        restore_static(static, db, target, apply=True)
        self.assertEqual((target / 'assets/logo.svg').read_text(), '<svg/>')
        with self.assertRaises(FileExistsError): restore_static(static, db, target, apply=True)
        before = [p['id'] for p in Store(self.store.root, public=True).read()['publications']]
        self.store.change(lambda s: s['publications'].clear())
        with patch('lab_backend.cli.getpass.getpass', return_value='synthetic-backup-password'):
            restore(self.store, db, apply=True)
        self.assertEqual([p['id'] for p in Store(self.store.root, public=True).read()['publications']], before)

    def test_static_backup_rejects_wrong_version_changed_content_and_self_output(self):
        db, static, _ = self.paired_backup()
        with self.assertRaises(ValueError): backup_static(self.store, self.web, self.root / 'wrong.zip', 'wrong', db)
        self.assertFalse(backup_state(self.store)['static']['ok'])
        with self.assertRaises(ValueError): backup_static(self.store, self.web, self.web / 'assets/self.zip', 'release-1', db)
        self.store.change(lambda s: s['people'][0].update(title='Changed'))
        with self.assertRaises(ValueError): backup_static(self.store, self.web, self.root / 'changed.zip', 'release-1', db)
        corrupted = self.root / 'bad.zip'
        with zipfile.ZipFile(static) as src, zipfile.ZipFile(corrupted, 'w') as dst:
            for name in src.namelist(): dst.writestr(name, b'changed' if name == 'assets/logo.svg' else src.read(name))
        with self.assertRaises(ValueError): verify_static(corrupted, db)
        mismatch = self.root / 'other.enc'; mismatch.write_bytes(b'not the matching database')
        with self.assertRaises(ValueError): verify_static(static, mismatch)

    def test_backup_failure_and_persistent_503_raise_alerts(self):
        with patch('lab_backend.cli.password_prompt', side_effect=ValueError('cancelled')):
            with self.assertRaises(ValueError): backup(self.store, self.root / 'failed.enc', version='failure')
        self.assertFalse(backup_state(self.store)['database']['ok'])
        probe = lambda service, endpoint: 200 if endpoint == 'healthz' else 503
        with patch('scripts.maintenance.shutil.disk_usage', return_value=type('Disk', (), {'free': 10})()), patch('sys.stderr', new_callable=io.StringIO) as err:
            for index in range(3):
                report = monitor(self.store, probe, min_free=20)
                alerts = {a['code'] for a in report['alerts']}
                self.assertEqual('CONTENT_UNAVAILABLE' in alerts, index == 2)
            self.assertTrue({'LOW_DISK', 'BACKUP_FAILED_OR_MISSING', 'BACKUP_OVERDUE'} <= alerts)
            self.assertIn('ALERT', err.getvalue())
            self.assertNotIn('PROCESS_UNAVAILABLE', alerts)
            recovered = monitor(self.store, lambda *_: 200, min_free=1)
            self.assertEqual(recovered['counters'], {'public-api': 0, 'admin-api': 0})
            (self.store.admin_path.parent / 'monitor-state.json').write_text('{broken')
            invalid = monitor(self.store, lambda *_: 200, min_free=1)
            self.assertIn('MONITOR_STATE_INVALID', {a['code'] for a in invalid['alerts']})

    def test_shared_locks_reject_competing_jobs_and_release_after_error(self):
        """維運工作不可並行；錯誤後鎖須釋放，仍拒絕符號連結。"""
        with self.assertRaises(ValueError):
            with self.store.maintenance():
                raise ValueError('simulated maintenance failure')
        with self.store.maintenance():
            pass
        private = self.store.admin_path.parent
        with exclusive_lock(private / 'backup.lock', create=True):
            with self.assertRaises(BlockingIOError):
                backup(self.store, self.root / 'blocked.enc', version='blocked')
        with exclusive_lock(private / 'monitor.lock', create=True):
            with self.assertRaises(BlockingIOError):
                monitor(self.store, lambda *_: self.fail('locked monitor must not probe'))
        (private / 'monitor.lock').unlink()
        target = self.root / 'keep'; target.write_text('unchanged')
        (private / 'monitor.lock').symlink_to(target)
        with self.assertRaises(OSError): monitor(self.store, lambda *_: 200)
        self.assertEqual(target.read_text(), 'unchanged')

    def test_static_limits_and_replaced_archive_do_not_leave_partial_restore(self):
        """串流超限或預覽後檔案變動，不能留下看似成功的恢復目錄。"""
        db, archive, manifest = self.paired_backup()
        with patch('scripts.maintenance.MAX_STATIC_TOTAL', 5):
            with self.assertRaises(ValueError): verify_static(archive, db)
        # 先核對原檔，套用開始前換成同大小但指紋不同的內容。
        def changed(*_):
            with zipfile.ZipFile(archive, 'w') as handle:
                handle.writestr('assets/logo.svg', '<bad/>')
                handle.writestr('manifest.json', json.dumps(manifest))
            return manifest
        target = self.root / 'incomplete-restore'
        with patch('scripts.maintenance.verify_static', side_effect=changed):
            with self.assertRaises(ValueError): restore_static(archive, db, target, apply=True)
        self.assertFalse(target.exists())
