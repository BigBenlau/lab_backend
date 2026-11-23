"""用暫存憑證及命令替身執行續期腳本。Docker、Nginx 和握手均不連網。"""
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import tempfile
import unittest


class CertificateTests(unittest.TestCase):
    ip = '203.0.113.10'
    domains = ('casino.ye.wang', 'crab.ye.wang', 'ye.wang')

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.bin = self.root / 'bin'
        self.state = self.root / 'state'
        self.cache = self.state / 'lego/certificates'
        self.mounts = [self.root / 'secrets' / name for name in ('public-tls', 'tls')]
        for path in [self.bin, self.cache, self.root / 'deploy', *self.mounts]:
            path.mkdir(parents=True)
        for name, value in {'ip': self.ip, 'email': 'ops@example.org', 'project': str(self.root)}.items():
            (self.state / name).write_text(value + '\n')
        self.trace = self.root / 'trace'
        self.env = {**os.environ, 'TEST_ROOT': str(self.root), 'PATH': str(self.bin) + ':' + os.environ['PATH']}
        self.openssl = shutil.which('openssl')
        if not self.openssl or 'OpenSSL 3.' not in subprocess.check_output([self.openssl, 'version'], text=True):
            self.skipTest('測試需要 OpenSSL 3，請將該版本加入 PATH')

        # 只改測試副本中的主機路徑、root 身份及 GNU 專用檔案選項。
        source = (Path(__file__).resolve().parents[1] / 'deploy/tls.sh').read_text()
        self.script = self.root / 'tls.sh'
        self.script.write_text(source.replace('state=/var/lib/lab-tls', 'state="$TEST_ROOT/state"')
                               .replace('trust=/etc/ssl/certs/ca-certificates.crt', 'trust="$TEST_ROOT/trust.pem"')
                               .replace('[[ $EUID == 0 ]]', 'true').replace(' -o 10001 -g 10001', '')
                               .replace('-m 0600 --', '-m 0600').replace('mv -fT --', 'mv -f'))
        (self.root / 'deploy/compose.yml').touch()
        (self.root / 'deploy/lab.sh').write_text('''
echo "lab $*" >> "$TEST_ROOT/trace"
case "$1" in
  ps) [ "${OFFLINE:-0}" = 1 ] || echo gateway ;;
  exec) exit 0 ;;
  *) exit 90 ;;
esac
''')
        self.command('flock', 'exit 0')
        self.command('sleep', 'exit 0')
        self.command('timeout', 'if [ "$1" = -k ]; then shift 3; else shift; fi\nexec "$@"')
        self.command('docker', '''
echo "docker $*" >> "$TEST_ROOT/trace"
case "$1" in
  image) exit 0 ;;
  inspect) exit 1 ;;
  run)
    while [ "$1" != --cert.name ]; do shift; done
    for ext in key crt; do cp "$TEST_ROOT/candidate.$ext" "$TEST_ROOT/state/lego/certificates/$2.$ext"; done ;;
  *) exit 91 ;;
esac
''')
        self.command('openssl', '''
if [ "$1" = s_client ]; then
  echo "$*" >> "$TEST_ROOT/trace"
  case "$*" in
    *127.0.0.1:27182*) [ "${FAIL_ADMIN:-0}" = 0 ] || exit 1; mount=tls ;;
    *) mount=public-tls ;;
  esac
  cat "$TEST_ROOT/secrets/$mount/cert.pem"
  exit
fi
exec ''' + shlex.quote(self.openssl) + ' "$@"')
        self.sans = [f'DNS:{name}' for name in self.domains] + [f'IP:{self.ip}']

    def command(self, name, body):
        path = self.bin / name
        path.write_text('#!/bin/sh\nset -eu\n' + body + '\n')
        path.chmod(0o700)

    def candidate(self, sans, days=6):
        """本地信任每張合成憑證，以便單獨檢查身份、到期時間與指紋。"""
        subprocess.run([self.openssl, 'req', '-x509', '-newkey', 'ec', '-pkeyopt', 'ec_paramgen_curve:P-256',
                        '-nodes', '-subj', '/CN=test', '-days', str(days), '-addext', 'subjectAltName=' + ','.join(sans),
                        '-out', 'candidate.crt', '-keyout', 'candidate.key'], cwd=self.root, check=True, capture_output=True)
        leaf = (self.root / 'candidate.crt').read_bytes()
        with (self.root / 'trust.pem').open('ab') as trust:
            trust.write(leaf)
        return leaf

    def invoke(self, *args, success=True, **env):
        self.trace.write_text('')
        result = subprocess.run(['bash', str(self.script), *args], cwd=self.root, env={**self.env, **env},
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode == 0, success, result.stderr)
        calls = self.trace.read_text()
        self.assertNotRegex(calls, r'lab (stop|restart|up|start) ')
        return calls

    def test_migration_renewal_and_rollback(self):
        # 遺留模式檔與有效的舊掛載不應讓新排程繼續只簽兩個名稱。
        (self.state / 'active').write_text('sites\n')
        self.candidate(self.sans[:2])
        for directory in self.mounts:
            for ext, name in [('crt', 'cert.pem'), ('key', 'key.pem')]:
                shutil.copyfile(self.root / f'candidate.{ext}', directory / name)
        leaf = self.candidate(self.sans[::-1])
        key = (self.root / 'candidate.key').read_bytes()
        calls = self.invoke('renew')
        order = shlex.split(next(line for line in calls.splitlines() if line.startswith('docker run ')))
        self.assertEqual([order[i + 1] for i, flag in enumerate(order) if flag == '--domains'], [*self.domains, self.ip])
        self.assertEqual(order[order.index('--profile') + 1], 'shortlived')
        self.assertEqual(order[order.index('--network') + 1], 'lab-backend_edge')
        self.assertNotIn('-p', order)
        self.assertEqual(calls.count('s_client '), 8)
        for port in (443, 27182):
            for name in self.domains:
                self.assertIn(f'127.0.0.1:{port} -servername {name} -verify_hostname {name}', calls)
            self.assertIn(f'127.0.0.1:{port} -noservername -verify_ip {self.ip}', calls)
        fresh = self.invoke('renew')
        self.assertNotIn('docker run', fresh)
        self.assertNotIn('nginx -s reload', fresh)

        # 同一組身份剩兩天就應續簽。若安裝後的後台握手失敗，兩端一起還原。
        self.candidate(self.sans, days=2)
        shutil.copyfile(self.root / 'candidate.crt', next(self.cache.glob('*.crt')))
        self.candidate(self.sans)
        calls = self.invoke('renew', success=False, FAIL_ADMIN='1')
        self.assertIn('docker run', calls)
        self.assertEqual(calls.count('nginx -s reload'), 2)
        for directory in self.mounts:
            self.assertEqual((directory / 'cert.pem').read_bytes(), leaf)
            self.assertEqual((directory / 'key.pem').read_bytes(), key)

    def test_incomplete_certificate_and_offline_bootstrap(self):
        for sans in [self.sans[:3], self.sans[:2] + self.sans[3:], self.sans[:3] + [f'DNS:{self.ip}'],
                     self.sans[:3] + ['IP:203.0.113.11'], self.sans + ['DNS:extra.example.org']]:
            with self.subTest(sans=sans):
                self.candidate(sans)
                self.invoke('issue', success=False, OFFLINE='1')
                self.assertFalse(any((path / 'cert.pem').exists() for path in self.mounts))
        self.candidate(self.sans)
        self.assertIn('-p 0.0.0.0:443:8443', self.invoke('issue', OFFLINE='1'))
        self.assertNotIn('docker run', self.invoke('renew', success=False, OFFLINE='1'))
        for old in ('sites', 'dual', 'ip', 'domain'):
            self.assertEqual(self.invoke(old, success=False), '')
