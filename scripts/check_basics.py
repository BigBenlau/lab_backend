"""僅語法/匯入/契約欄位/設定一致性；不啟動服務、不做功能或安全驗收。"""
from pathlib import Path
import argparse
import ast
import importlib
import re
import subprocess
import sys
import sqlite3
import tomllib
import yaml

ROOT = Path(__file__).resolve().parents[1]
argparse.ArgumentParser(description=__doc__).parse_args()
from sync_contracts import sync
# 檢查只比對，不改檔；需要更新契約時明確執行sync_contracts.py。
specs = sync(check=True)
sys.path.insert(0, str(ROOT))
assert sys.version_info[:2] == (3, 14), '請使用 Python 3.14'
assert sqlite3.sqlite_version_info >= (3, 37, 0), 'SQLite 需支援 STRICT 表'
for source in (ROOT / 'lab_backend').glob('*.py'):
    ast.parse(source.read_text(), filename=str(source))
    importlib.import_module('lab_backend.' + source.stem)
ast.parse(Path(__file__).read_text())
tomllib.loads((ROOT / 'pyproject.toml').read_text())

from lab_backend.models import ALL_MODELS, MODELS, FILES
from lab_backend.store import TABLES, columns, field_type
for model in ALL_MODELS.values():
    model.model_json_schema()
assert len(FILES) == 5
assert all('publishState' not in model.model_fields and 'publishedAt' not in model.model_fields for model in MODELS.values())
assert not {'slug', 'sourceUrl', 'affiliation'} & MODELS['people'].model_fields.keys()
assert 'slug' not in MODELS['publications'].model_fields

# sync已建立並比對兩份契約，不重建app或再次寫入相同文件。
public, admin = specs['public'], specs['admin']
assert not any('/admin/' in p or '/auth/' in p for p in public['paths'])
assert '/api/v1/public/home' in public['paths']
assert '/api/v1/admin/content/{resource}' in admin['paths']
for name, contract, count in [('public', public, 4), ('admin', admin, 14)]:
    assert sum(len(operations) for operations in contract['paths'].values()) == count
    for operations in contract['paths'].values():
        for operation in operations.values():
            # 不允許管理回應退回無型別的 {}，或誤用包含原始輸入的框架錯誤模型。
            for code, response in operation['responses'].items():
                shape = response['content']['application/json']['schema']
                assert shape, '每個回應都必須有明確 JSON schema'
                if code == '422':
                    assert shape == {'$ref': '#/components/schemas/ApiError'}
expected_paths = {'/api/v1/public/' + name for name in ('home', 'people', 'people/{person_id}', 'publications')}
assert set(public['paths']) == expected_paths, '公開僅保留四個頁面端點'
for route in public['paths'].values():
    assert set(route) == {'get'}
assert set(public['components']['schemas']['Home']['properties']) == {'assets', 'recentPublications', 'counts'}
assert set(public['components']['schemas']['PersonDetail']['properties']) == {'person', 'publications'}
assert 'publicationIds' not in public['components']['schemas']['PersonSummary']['properties']
assert set(public['components']['schemas']['Counts']['properties']) == {'publications'}
assert 'englishName' in public['components']['schemas']['PersonSummary']['properties']
for model in ('PersonSummary', 'PublicPerson'):
    assert 'alumniSince' in public['components']['schemas'][model]['properties']
    assert 'alumniSince' in public['components']['schemas'][model]['required']
assert 'alumniSince' not in admin['components']['schemas']['Person']['required']
assert MODELS['people'].model_fields['alumniSince'].default is None
assert 'graduationDate' not in MODELS['people'].model_fields
assert not {'preferredName', 'slug', 'createdAt', 'updatedAt', 'affiliation', 'publicationIds'} & public['components']['schemas']['PublicPerson']['properties'].keys()
assert set(public['components']['schemas']['PublicationSummary']['properties']) == {'id', 'title', 'year', 'type', 'venue'}
assert set(admin['components']['schemas']['Access']['properties']) == {'active', 'version'}
assert not any('/umpass/' in path for path in admin['paths'])
assert not any('enrollment' in path or 'reauthenticate' in path for path in admin['paths'])
assert set(admin['components']['schemas']['Credentials']['properties']) == {'login', 'password'}
resource_parameter = next(p for p in admin['paths']['/api/v1/admin/content/{resource}']['get']['parameters'] if p['name'] == 'resource')
assert set(resource_parameter['schema']['enum']) == set(MODELS), '路徑資源白名單須與內容模型一致'
assert set(admin['components']['schemas']['Edit']['required']) == {'data', 'version'}
assert set(admin['components']['schemas']['ContentRecord']['properties']) == {'data', 'version', 'created', 'updated'}
assert set(admin['components']['schemas']['UserInfo']['properties']) == {'id', 'displayName', 'login', 'role', 'active', 'version', 'createdAt'}
assert set(admin['components']['schemas']['CreateContent']['properties']['data']['anyOf'][i]['$ref'].split('/')[-1]
           for i in range(3)) == {model.__name__ for model in MODELS.values()}
# 管理契約表也是功能清單：新增 HTTP 操作時必須補上 UI 對應入口。
documented = {(method.lower(), path) for method, path in re.findall(
    r'^\| (GET|POST|PUT|PATCH|DELETE) (/api/v1/(?:admin|auth)/[^ ]+) \|', (ROOT / 'docs/API.md').read_text(), re.M)}
assert documented == {(method, path) for path, operations in admin['paths'].items() for method in operations}, '管理 API／介面對應表過期'
documented_public = set(re.findall(r'^\| GET (/api/v1/public/[^ ]+) \|', (ROOT / 'docs/API.md').read_text(), re.M))
assert documented_public == set(public['paths']), '公開 API 清單過期'

# 資料字典逐表列出真實欄位；修改模型時不得只更新 OpenAPI 而漏改 SQLite 文件。
dictionary = (ROOT / 'docs/DATABASE.md').read_text()
assert set(re.findall(r'^### (\w+)$', dictionary, re.M)) == set(TABLES.values())
for resource in ALL_MODELS:
    section = re.search(rf'^### {TABLES[resource]}\n(.*?)(?=^##|\Z)', dictionary, re.S | re.M).group(1)
    rows = re.findall(r'^\| `(\w+)` \| ([^|]+) \| ([^|]+) \|', section, re.M)
    expected = columns(resource)
    assert [row[0] for row in rows] == expected, f'{resource} 的資料字典欄位過期'
    for key, sql_type, nullable in rows:
        kind, null = field_type(resource, key)
        expected_type = 'INTEGER' if kind in ('integer', 'boolean') else 'REAL' if kind == 'number' else 'TEXT'
        assert sql_type.startswith(expected_type) and nullable.startswith('是' if null else '否')

compose = yaml.safe_load((ROOT / 'deploy/compose.yml').read_text())
services = compose['services']
assert set(services) == {'public-web', 'gateway', 'public-api', 'admin-api', 'tools'}
for name, service in services.items():
    assert service['read_only'] and service['cap_drop'] == ['ALL']
    assert service['user'] == '10001:10001'
    for mount in service['tmpfs']:
        assert mount.startswith('/tmp:') and 'size=' in mount and 'nosuid' in mount
    if name not in ('gateway', 'public-web'):
        assert not service.get('ports')
public_mounts = services['public-api']['volumes']
assert {v['target'] for v in public_mounts} == {'/data/content'}
assert all(v['read_only'] for v in public_mounts)
assert public_mounts[0]['volume'] == {'subpath': 'content', 'nocopy': True}
assert public_mounts[0]['type'] == 'volume' and public_mounts[0]['source'] == 'lab_data'
assert services['admin-api']['volumes'] == services['tools']['volumes'] == [
    {'type': 'volume', 'source': 'lab_data', 'target': '/data'}]
assert compose['volumes'] == {'lab_data': {'driver': 'local'}}
assert services['tools']['profiles'] == ['maintenance']
assert services['tools']['healthcheck'] == {'disable': True}
assert set(services['tools']['networks']) == {'frontend_net', 'admin_net'}
assert all('docker.sock' not in str(service) for service in services.values())
assert all('/data' not in v['target'] for v in services['gateway']['volumes'])
assert services['admin-api']['command'].count('1') == 1
assert services['admin-api']['environment']['LAB_DEV'] == 'false'
assert not set(services['public-api']['networks']) & set(services['admin-api']['networks'])
assert not set(services['public-api']['networks']) & set(services['gateway']['networks'])
assert services['gateway']['ports'] == ['0.0.0.0:${ADMIN_HTTPS_PORT:-27182}:27182']
assert services['admin-api']['environment']['LAB_ADMIN_ORIGIN'] == services['tools']['environment']['LAB_ADMIN_ORIGIN'] == 'https://${ADMIN_HOST:?Set admin hostname}:${ADMIN_HTTPS_PORT:-27182}'
assert not services['gateway'].get('depends_on')
assert compose['networks']['frontend_net']['internal'] is True
assert services['public-api']['networks']['frontend_net']['aliases'] == ['lab-public-api']
nginx = (ROOT / 'deploy/nginx.conf.template').read_text()
assert '8443' not in nginx and '/api/v1/public/' not in nginx
assert 'resolver 127.0.0.11' in nginx and 'proxy_pass http://$admin_upstream' in nginx
assert 'X-Real-IP $remote_addr' in nginx
assert 'listen 27182 ssl default_server;' in nginx
assert 'server_name ${ADMIN_HOST};' in nginx and 'if ($host != "${ADMIN_HOST}")' in nginx
assert 'set_real_ip_from' not in nginx and 'real_ip_header' not in nginx
assert not re.search(r'^\s*(allow|deny|satisfy)\s', nginx, re.M), '來源 IP 僅由 Cloud 防火牆管理'
assert 'admin-allowlist' not in nginx and 'limit_req zone=login_rate' in nginx
assert 'location ~ ^/api/v1/(auth|admin)/' in nginx and 'location /api/ { return 404; }' in nginx
assert set(services['gateway']['environment']) == {'ADMIN_HOST', 'ADMIN_HTTPS_PORT'}
entrypoint = (ROOT / 'deploy/gateway-entrypoint.sh').read_text()
assert 'ADMIN_ALLOW_CIDRS' not in entrypoint and 'nginx -t -c /tmp/nginx.conf' in entrypoint

# 官網只讀 public 子目錄；API 路徑白名單不得擴張到管理或探測端點。
web = services['public-web']
assert web['build'] == {'context': '..', 'dockerfile': 'public_frontend/Dockerfile'}
assert all(service['build']['context'] == '..' for service in services.values())
subprocess.run(['sh', str(ROOT / 'deploy/lab.sh'), 'check-web'], check=True)
if (ROOT / '.git').exists():
    # 僅在 Git 工作目錄檢查索引，阻止「本機有檔案、clone 卻只有 gitlink」再次漏檢。
    entries = subprocess.check_output(['git', 'ls-files', '--stage', '--', 'public_frontend'], cwd=ROOT, text=True)
    tracked = {}
    for entry in entries.splitlines():
        metadata, path = entry.split('\t', 1)
        mode, _, stage = metadata.split()
        assert stage == '0' and mode != '160000', 'public_frontend仍為gitlink或有未解衝突，不能從Git取得完整官網'
        tracked[path] = mode
    for name in ('Dockerfile', 'nginx.conf', 'nginx-https.conf', 'nginx-http.conf',
                 'dist/lab.html', 'dist/lab.js', 'dist/api.js', 'dist/lab.css', 'dist/gold.css', 'contracts/public-api.d.ts'):
        assert tracked.get('public_frontend/' + name) in ('100644', '100755'), f'官網必要檔案尚未納入Git索引：{name}'
    assert not any(path.startswith(('public_frontend/dist/data/', 'public_frontend/dist/assets/')) or Path(path).name == 'lab.json'
                   for path in tracked), '初始化內容不得納入官網程式版本'
assert set(web['networks']) == {'public_edge', 'frontend_net'}
assert web['ports'] == ['0.0.0.0:443:8443']
assert web['volumes'][0] == {'type': 'volume', 'source': 'lab_data', 'target': '/srv/public',
                             'read_only': True, 'volume': {'subpath': 'public', 'nocopy': True}}
assert all(v['read_only'] for v in web['volumes'])
proxy = (ROOT / 'public_frontend/nginx.conf').read_text()
for key in ('home', 'people', 'publications'):
    assert f'/api/{key} /api/v1/public/{key};' in proxy
assert '/api/v1/admin/' not in proxy and '/api/v1/auth/' not in proxy
assert '/api/schemaVersion' not in proxy and '/api/lab' not in proxy and '/api/assets' not in proxy
assert '/api/v1/public/people/$person_id;' in proxy
assert 'if ($public_path = "") { return 404; }' in proxy
assert 'if ($request_method != GET)' in proxy and 'proxy_cache off;' in proxy
assert 'location = /data/site.json { alias /srv/public/site.json; }' in proxy
assert 'disable_symlinks on;' in proxy and 'location / { return 404; }' in proxy
assert 'set_real_ip_from' not in proxy and 'real_ip_header' not in proxy
assert 'include /etc/nginx/transport.conf;' in proxy
assert 'map $scheme $hsts' in proxy and 'add_header Strict-Transport-Security $hsts always;' in proxy
https_transport = (ROOT / 'public_frontend/nginx-https.conf').read_text()
http_transport = (ROOT / 'public_frontend/nginx-http.conf').read_text()
assert 'listen 8443 ssl default_server;' in https_transport
assert 'ssl_certificate /run/tls/cert.pem;' in https_transport
assert 'ssl_certificate_key /run/tls/key.pem;' in https_transport
assert 'listen 8080 default_server;' in http_transport
assert not re.search(r'\b(ssl|ssl_certificate|return|add_header)\b', http_transport)

# 保留 !override 標記來檢查取代語意，不以普通清單假裝 Compose 已合併成功。
# 真實 Compose 合併、Nginx 解析與端口可達性仍須在部署環境驗證。
class ReplaceList(list):
    """標記必須整份取代的端口／掛載，防止 HTTP 仍依賴 TLS。"""

class ComposeLoader(yaml.SafeLoader):
    """只額外接受專案 HTTP 覆蓋檔使用的 Compose 標記。"""

ComposeLoader.add_constructor('!override', lambda loader, node: ReplaceList(loader.construct_sequence(node, deep=True)))
http_override = yaml.load((ROOT / 'deploy/compose.http.yml').read_text(), Loader=ComposeLoader)
assert set(http_override) == {'services'}
assert set(http_override['services']) == {'public-web', 'admin-api', 'gateway'}
http_web = http_override['services']['public-web']
assert set(http_web) == {'ports', 'volumes'}, 'HTTP 必須沿用原映像、內網及權限設定'
assert isinstance(http_web['ports'], ReplaceList) and http_web['ports'] == ['0.0.0.0:${PUBLIC_HTTP_PORT:-80}:8080']
assert 'PUBLIC_HTTP_PORT=80' in (ROOT / '.env.example').read_text().splitlines()
assert 'PUBLIC_HTTP_PORT' not in (ROOT / 'deploy/compose.yml').read_text(), 'HTTP測試端口不得影響正式HTTPS或管理端'
assert isinstance(http_web['volumes'], ReplaceList)
assert http_web['volumes'] == [web['volumes'][0], {
    'type': 'bind', 'source': '../public_frontend/nginx-http.conf', 'target': '/etc/nginx/transport.conf',
    'read_only': True, 'bind': {'create_host_path': False}}]
assert all(http_override['services'][name] == {'profiles': ['https-admin']} for name in ('admin-api', 'gateway'))
for config in (proxy, nginx):
    for name in ('client_body', 'proxy', 'fastcgi', 'uwsgi', 'scgi'):
        assert f'{name}_temp_path /tmp/' in config
    assert 'proxy_max_temp_file_size 0;' in config
# 單一白名單只允許明確程式檔；開放目錄後立即重新排除其內容，防止連帶放行資料。
build_ignore = [line for line in (ROOT / '.dockerignore').read_text().splitlines() if line and not line.startswith('#')]
build_dirs = {'lab_backend', 'scripts', 'admin_frontend', 'admin_frontend/admin', 'deploy',
              'public_frontend', 'public_frontend/dist'}
build_files = {'requirements.lock', 'scripts/maintenance.py', 'scripts/prepare_initial_data.py',
               'deploy/Dockerfile', 'deploy/Dockerfile.gateway', 'deploy/nginx.conf.template', 'deploy/gateway-entrypoint.sh',
               'public_frontend/Dockerfile', 'public_frontend/nginx.conf', 'public_frontend/nginx-https.conf'}
build_files.update(str(path.relative_to(ROOT)) for path in (ROOT / 'lab_backend').glob('*.py'))
build_files.update('admin_frontend/admin/' + name for name in ('index.html', 'app.js', 'style.css'))
build_files.update('public_frontend/dist/' + name for name in ('lab.html', 'lab.js', 'api.js', 'lab.css', 'gold.css'))
expected_rules = {'**'} | {'!' + path for path in build_files}
for directory in build_dirs:
    expected_rules.update({'!' + directory + '/', directory + '/**'})
    assert build_ignore.index(directory + '/**') == build_ignore.index('!' + directory + '/') + 1
assert build_ignore[0] == '**' and set(build_ignore) == expected_rules
assert len(build_ignore) == len(expected_rules), '共用建置白名單不應有重複規則'
for path in build_files:
    assert (ROOT / path).is_file()
    for parent in Path(path).parents:
        if str(parent) != '.':
            assert build_ignore.index('!' + path) > build_ignore.index(str(parent) + '/**')
for directory in ('public_frontend', 'deploy', 'admin_frontend', 'lab_backend', 'scripts'):
    assert not (ROOT / directory / '.gitignore').exists(), 'Git排除規則統一放在根目錄'
    assert not list((ROOT / directory).glob('*.dockerignore')), '不能以子目錄或Dockerfile專用規則覆蓋根白名單'
public_docker = (ROOT / 'public_frontend/Dockerfile').read_text()
# 官網映像只COPY七個指定檔案，不能順便取得共用context中的管理頁或Python程式。
public_sources = {source for line in public_docker.splitlines() if line.startswith('COPY ') for source in line.split()[1:-1]}
assert public_sources == {path for path in build_files if path.startswith('public_frontend/') and not path.endswith('/Dockerfile')}
assert 'COPY public_frontend/nginx-https.conf /etc/nginx/transport.conf' in public_docker
for source in (ROOT / 'scripts').glob('*.py'):
    ast.parse(source.read_text(), filename=str(source))
assert 'lab.json' not in (ROOT / 'deploy/Dockerfile').read_text()

subprocess.run(['node', '--check', str(ROOT / 'admin_frontend/admin/app.js')], check=True)
for source in (ROOT / 'public_frontend/dist').glob('*.js'):
    subprocess.run(['node', '--check', str(source)], check=True)
for source in (ROOT / 'deploy').glob('*.sh'):
    subprocess.run(['sh', '-n', str(source)], check=True)
print('PASS: Python imports, public/admin contracts, 5-table dictionary, YAML boundaries, JS/shell syntax.')
print('Scope: basic checks only. Regression suite is a separate command; no HTTP listener, Docker or deployment here.')
