"""使用合成資料與暫存目錄驗證容量、檔案維護及監測；不執行Docker。"""
import asyncio
import copy
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
import urllib.error
from unittest.mock import patch
import zipfile

from fixtures import sample_data, state_from_sample
import test_regressions as helpers
from lab_backend.application import public_app
from lab_backend.cli import backup, backup_state, file_hash, import_csv, restore
from lab_backend.models import MODELS, new_metadata
from lab_backend.store import Store, StoreError, encode_csv, exclusive_lock, json_size, validate_capacity
from scripts.maintenance import backup_static, check_files, container_probe, main, monitor, restore_static, verify_static


class GatewaySettingsTests(unittest.TestCase):
    """在暫存目錄用命令替身驗證啟動邊界；不執行真正Nginx／Docker。"""
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='lab-gateway-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        deploy = Path(__file__).resolve().parents[1] / 'deploy'
        (self.root / 'nginx.conf.template').write_text((deploy / 'nginx.conf.template').read_text())
        self.entry = self.root / 'entry.sh'
        self.entry.write_text((deploy / 'gateway-entrypoint.sh').read_text()
                              .replace('/etc/lab/nginx.conf.template', str(self.root / 'nginx.conf.template'))
                              .replace('/tmp/', str(self.root) + '/'))
        # 替身只記錄呼叫及模擬設定檢查失敗，不開socket、不解析真實憑證。
        for name, body in {
            'envsubst': 'cat',
            'nginx': 'printf "%s\\n" "$*" >> "$GATEWAY_CALLS"\n'
                     'if [ "$1" = "-t" ]; then exit "${GATEWAY_CHECK_EXIT:-0}"; fi',
        }.items():
            path = self.root / name
            path.write_text('#!/bin/sh\n' + body + '\n')
            path.chmod(0o700)
        self.env = {**os.environ, 'PATH': str(self.root) + os.pathsep + os.environ['PATH'],
                    'ADMIN_HOST': 'admin.example', 'ADMIN_HTTPS_PORT': '27182',
                    'LAB_HTTP_TEST': 'false',
                    'GATEWAY_CALLS': str(self.root / 'calls')}

    def run_entry(self, **changes):
        """每次獨立啟動並捕捉退出碼，不讓被拒的設定污染真實檔案。"""
        (self.root / 'calls').unlink(missing_ok=True)
        return subprocess.run(['sh', str(self.entry)], env={**self.env, **changes},
                              text=True, capture_output=True, timeout=5)

    def test_default_and_custom_port_without_local_ip_rules(self):
        """預設及自訂端口均可啟動；來源IP由Cloud管理，不要求本機清單。"""
        for port in ('', '27182', '31827'):
            with self.subTest(port=port):
                result = self.run_entry(ADMIN_HTTPS_PORT=port)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertFalse((self.root / 'admin-allowlist.conf').exists())
                calls = (self.root / 'calls').read_text().splitlines()
                self.assertEqual(len(calls), 2)
                self.assertTrue(calls[0].startswith('-t -c '))
                self.assertTrue(calls[1].endswith('-g daemon off;'))
                transport = (self.root / 'transport.conf').read_text()
                self.assertIn('listen 27182 ssl default_server;', transport)
                self.assertIn('ssl_certificate /run/tls/cert.pem;', transport)
                self.assertIn('ssl_certificate_key /run/tls/key.pem;', transport)
                self.assertIn('ssl_protocols TLSv1.2 TLSv1.3;', transport)

    def test_http_needs_no_certificate_and_switching_back_restores_tls(self):
        """顯式 HTTP 共用路由但不載入憑證；切回正式模式必須重新產生 TLS 設定。"""
        for port in ('', '27181', '31827'):
            result = self.run_entry(LAB_HTTP_TEST='true', ADMIN_HOST='192.0.2.10', ADMIN_HTTP_PORT=port)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual((self.root / 'transport.conf').read_text(), 'listen 27182 default_server;\n')
        self.assertEqual(self.run_entry().returncode, 0)
        self.assertIn('ssl_certificate ', (self.root / 'transport.conf').read_text())
        for port in ('80', '0', '027181', '65536', '27181;'):
            self.assertNotEqual(self.run_entry(LAB_HTTP_TEST='true', ADMIN_HTTP_PORT=port).returncode, 0)
            self.assertFalse((self.root / 'calls').exists())

    def test_bad_settings_never_start_nginx(self):
        """維持端口範圍與Host設定注入檢查；不把雲端規則混入程式設定。"""
        cases = [('ADMIN_HTTPS_PORT', value) for value in ('443', '0', '027182', '65536', '-1', '27182;')]
        cases += [('ADMIN_HOST', value) for value in ('', 'https://admin.example', 'admin.example; include /tmp/other;')]
        cases += [('LAB_HTTP_TEST', value) for value in ('TRUE', '1', 'http;')]
        for key, value in cases:
            with self.subTest(key=key, value=value):
                self.assertNotEqual(self.run_entry(**{key: value}).returncode, 0)
                self.assertFalse((self.root / 'calls').exists())

    def test_nginx_check_failure_stops_startup(self):
        """TLS／Nginx設定檢查失敗時不能繼續開管理入口。"""
        self.assertNotEqual(self.run_entry(GATEWAY_CHECK_EXIT='1').returncode, 0)
        self.assertEqual(len((self.root / 'calls').read_text().splitlines()), 1)


class DeploymentFilesTests(unittest.TestCase):
    """用暫存來源及命令替身核對建置邊界；不呼叫真正Docker。"""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='lab deploy ')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / 'deploy').mkdir()
        self.entry = self.root / 'deploy/lab.sh'
        self.entry.write_text((Path(__file__).resolve().parents[1] / 'deploy/lab.sh').read_text())
        (self.root / '.dockerignore').write_text('**\n')
        binary = self.root / 'bin'
        binary.mkdir()
        docker = binary / 'docker'
        docker.write_text('#!/bin/sh\nprintf "%s\\n" "$@" > "$LAB_DOCKER_CALLS"\n')
        docker.chmod(0o700)
        self.calls = self.root / 'calls'
        self.env = {**os.environ, 'PATH': str(binary) + os.pathsep + os.environ['PATH'],
                    'LAB_DOCKER_CALLS': str(self.calls)}

    def run_command(self, *args):
        """保留含空格的專案路徑，驗證檢查與Compose參數都正確引用。"""
        return subprocess.run(['sh', str(self.entry), *args], env=self.env,
                              capture_output=True, text=True, timeout=5)

    def test_missing_dockerfile_stops_before_docker(self):
        for args in [('http', 'build', '--pull', 'public-api', 'public-web'), ('build', '--pull')]:
            result = self.run_command(*args)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('public_frontend/Dockerfile', result.stderr)
            self.assertFalse(self.calls.exists())

    def test_backend_build_does_not_require_frontend(self):
        result = self.run_command('http', 'build', '--pull', 'public-api')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.calls.read_text().splitlines()[-3:], ['build', '--pull', 'public-api'])

    def test_complete_code_without_initial_data_builds_and_empty_ignore_is_rejected(self):
        for name in ('Dockerfile', 'nginx.conf', 'nginx-https.conf', 'nginx-http.conf',
                     'dist/lab.html', 'dist/lab.js', 'dist/api.js', 'dist/lab.css', 'dist/gold.css'):
            path = self.root / 'public_frontend' / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('# synthetic source\n')
        result = self.run_command('check-web')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.calls.exists())
        result = self.run_command('http', 'build', '--pull', 'public-api', 'public-web')
        self.assertEqual(result.returncode, 0, result.stderr)
        args = self.calls.read_text().splitlines()
        self.assertIn(str(self.root / 'deploy/compose.http.yml'), args)
        self.assertEqual(args[-4:], ['build', '--pull', 'public-api', 'public-web'])
        self.calls.unlink()
        (self.root / '.dockerignore').write_text('')
        for args in [('build', '--pull'), ('http', 'build', 'public-api')]:
            result = self.run_command(*args)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('.dockerignore', result.stderr)
            self.assertFalse(self.calls.exists())

    def test_root_gitignore_protects_data_and_keeps_deployable_code(self):
        """用真正Git比對合成路徑；不讀私人檔案、不更改本專案索引。"""
        (self.root / '.gitignore').write_text((Path(__file__).resolve().parents[1] / '.gitignore').read_text())
        subprocess.run(['git', 'init', '-q', str(self.root)], check=True, capture_output=True)
        private = ['lab.json', 'public_frontend/dist/data/lab.json', 'public_frontend/dist/data/extra.json',
                   'public_frontend/dist/assets/photo.png', 'public_frontend/dist/assets/papers/example.pdf',
                   '.env', '.env.local', 'public_frontend/.env', 'public_frontend/.env.example',
                   'secrets/tls/key.pem', 'elsewhere/key.pem', 'elsewhere/test.key', 'elsewhere/test.p12',
                   'elsewhere/test.pfx', 'storage/content/content.db', 'copy.db-journal', 'copy.db-mj123',
                   'copy.sqlite-wal', 'copy.sqlite3-shm', 'copy.csv', 'copy.enc', 'backup.tar.gz',
                   'backup.zip', 'imports/people.csv', 'exports/data.json', 'backups/manifest.json',
                   '.venv/bin/python', 'public_frontend/node_modules/example/index.js', '__pycache__/app.pyc']
        code = ['.env.example', '.gitignore', '.dockerignore', 'deploy/Dockerfile', 'deploy/compose.yml',
                'public_frontend/Dockerfile', 'public_frontend/nginx-http.conf', 'public_frontend/dist/lab.html',
                'public_frontend/dist/api.js', 'public_frontend/contracts/public-api.d.ts',
                'docs/public-openapi.json', 'lab_backend/store.py', 'scripts/prepare_initial_data.py']
        result = subprocess.run(['git', '-c', 'core.excludesFile=/dev/null', 'check-ignore', '--no-index', '--stdin'],
                                cwd=self.root, input='\n'.join(private + code) + '\n', text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(set(result.stdout.splitlines()), set(private))


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
        (self.web / 'site.json').write_text(json.dumps({'siteSettings': {'name': 'Synthetic Lab'}}))

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

    def test_container_probe_uses_internal_targets_and_admin_host(self):
        """不開socket：核對管理Host、禁止外部proxy及503／連線失敗的分別。"""
        with patch('scripts.maintenance.urllib.request.build_opener') as build, \
                patch('scripts.maintenance.urllib.request.ProxyHandler') as proxy:
            opener = build.return_value
            opener.open.return_value.__enter__.return_value.status = 200
            self.assertEqual(container_probe('admin-api', 'readyz', 'https://admin.example:27182'), 200)
            request = opener.open.call_args.args[0]
            self.assertEqual(request.full_url, 'http://admin-api:8000/readyz')
            self.assertEqual(request.get_header('Host'), 'admin.example:27182')
            self.assertIsNone(request.get_header('Cookie'))
            proxy.assert_called_with({})
            error = urllib.error.HTTPError('http://public-api:8000/readyz', 503, 'unavailable', {}, None)
            opener.open.side_effect = error
            self.assertEqual(container_probe('public-api', 'readyz', ''), 503)
            self.assertIsNone(opener.open.call_args.args[0].get_header('Host'))
            opener.open.side_effect = urllib.error.URLError('unavailable')
            self.assertEqual(container_probe('public-api', 'healthz', ''), 0)
            with self.assertRaises(ValueError):
                container_probe('external.example', 'readyz', '')

    def test_monitor_interval_continues_after_alert(self):
        """常駐監測告警後仍採樣；單次命令保留退出碼供操作者判讀。"""
        with patch.dict(os.environ, {'LAB_STORAGE': str(self.store.root)}), \
                patch('scripts.maintenance.monitor', return_value={'ok': False}) as sample, \
                patch('scripts.maintenance.time.sleep', side_effect=[None, KeyboardInterrupt]), \
                patch('sys.stdout', new_callable=io.StringIO):
            with patch('sys.argv', ['maintenance', 'monitor', '--interval', '60']):
                with self.assertRaises(KeyboardInterrupt): main()
            self.assertEqual(sample.call_count, 2)
            with patch('sys.argv', ['maintenance', 'monitor']):
                self.assertEqual(main(), 1)

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
