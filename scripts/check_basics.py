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
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--write-openapi', action='store_true', help='同步輸出 public/admin 契約，不啟動服務或讀取業務資料')
args = parser.parse_args()
from sync_contracts import sync
specs = sync(check=not args.write_openapi)
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
assert set(services) == {'gateway', 'public-api', 'admin-api'}
for name, service in services.items():
    assert service['read_only'] and service['cap_drop'] == ['ALL']
    assert service['user'] == '10001:10001'
    for mount in service['tmpfs']:
        assert mount.startswith('/tmp:') and 'size=' in mount and 'nosuid' in mount
    if name != 'gateway':
        assert not service.get('ports')
public_mounts = services['public-api']['volumes']
assert {v['target'] for v in public_mounts} == {'/data/content'}
assert all(v['read_only'] for v in public_mounts)
assert {v['target'] for v in services['admin-api']['volumes']} == {'/data/content', '/data/private'}
assert all('/data' not in v['target'] for v in services['gateway']['volumes'])
assert services['admin-api']['command'].count('1') == 1
assert services['admin-api']['environment']['LAB_DEV'] == 'false'
assert not set(services['public-api']['networks']) & set(services['admin-api']['networks'])
assert not set(services['public-api']['networks']) & set(services['gateway']['networks'])
assert len(services['gateway']['ports']) == 1 and ':9443:9443' in services['gateway']['ports'][0]
assert not services['gateway'].get('depends_on')
assert compose['networks']['frontend_net']['internal'] is True
assert services['public-api']['networks']['frontend_net']['aliases'] == ['lab-public-api']
nginx = (ROOT / 'deploy/nginx.conf.template').read_text()
assert '8443' not in nginx and '/api/v1/public/' not in nginx
assert 'resolver 127.0.0.11' in nginx and 'proxy_pass http://$admin_upstream' in nginx
assert 'X-Real-IP $remote_addr' in nginx
assert 'ADMIN_ALLOW_CIDR' not in nginx and 'deny all;' not in nginx
assert 'location ~ ^/api/v1/(auth|admin)/' in nginx and 'location /api/ { return 404; }' in nginx
assert set(services['gateway']['environment']) == {'ADMIN_HOST'}

# 同源代理只放行四個頁面端點，不能轉送任意 API 路徑。
proxy = (ROOT / 'deploy/frontend-proxy.example.conf').read_text()
for key in ('home', 'people', 'publications'):
    assert f'location = /api/{key} {{' in proxy
    assert f'proxy_pass http://$lab_backend/api/v1/public/{key};' in proxy
assert '/api/v1/admin/' not in proxy and '/api/v1/auth/' not in proxy
assert '/api/schemaVersion' not in proxy and '/api/lab' not in proxy and '/api/assets' not in proxy
assert 'proxy_pass http://$lab_backend/api/v1/public/people/$person_id;' in proxy
assert 'lab.json' not in (ROOT / 'deploy/Dockerfile').read_text()
assert 'lab.json' in (ROOT / '.dockerignore').read_text().splitlines()

subprocess.run(['node', '--check', str(ROOT / 'admin_frontend/admin/app.js')], check=True)
for source in (ROOT / 'public_frontend/dist').glob('*.js'):
    subprocess.run(['node', '--check', str(source)], check=True)
subprocess.run(['sh', '-n', str(ROOT / 'deploy/gateway-entrypoint.sh')], check=True)
print('PASS: Python imports, public/admin contracts, 5-table dictionary, YAML boundaries, JS/shell syntax.')
print('Scope: basic checks only. Regression suite is a separate command; no HTTP listener, Docker or deployment here.')
