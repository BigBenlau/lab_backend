"""本次修正的本機回歸；僅 TemporaryDirectory/程序內 ASGI，不開 port、不用 Docker。"""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing, contextmanager
import copy
import json
import os
import re
from pathlib import Path
import sys
import tempfile
import sqlite3
import threading
from http.cookies import SimpleCookie
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from cryptography.fernet import InvalidToken
from fastapi import HTTPException
from lab_backend.application import admin_app, public_app
from lab_backend.cli import backup, restore, import_csv, init, local_admin, read_backup
from lab_backend.config import Config
from lab_backend.models import FILES, META, MODELS, new_metadata, Asset, Author, Home, Person, Publication, User, empty_state, data_only
from fixtures import sample_data, state_from_sample
from lab_backend.auth import PASSWORDS, audit
from lab_backend.store import TABLES, Store, StoreError, decode_csv, encode_csv, write_new


async def request(app, path, *, method='GET', body=None, token='', csrf='', include_headers=False):
    raw = b'' if body is None else json.dumps(body).encode()
    headers = [(b'host', b'127.0.0.1'), (b'origin', b'http://127.0.0.1'), (b'content-type', b'application/json')]
    if token:
        headers.append((b'cookie', f'lab_session_dev={token}'.encode()))
    if csrf:
        headers.append((b'x-csrf-token', csrf.encode()))
    path, _, query = path.partition('?')
    scope = {'type': 'http', 'asgi': {'version': '3.0'}, 'http_version': '1.1', 'scheme': 'http',
             'method': method, 'path': path, 'raw_path': path.encode(), 'query_string': query.encode(),
             'root_path': '', 'headers': headers, 'client': ('127.0.0.1', 1234), 'server': ('127.0.0.1', 80)}
    sent, output = False, []
    async def receive():
        nonlocal sent
        if not sent:
            sent = True
            return {'type': 'http.request', 'body': raw, 'more_body': False}
        return {'type': 'http.disconnect'}
    async def send(message):
        output.append(message)
    await app(scope, receive, send)
    status = next(m['status'] for m in output if m['type'] == 'http.response.start')
    data = b''.join(m.get('body', b'') for m in output if m['type'] == 'http.response.body')
    result = (status, json.loads(data) if data else None)
    if include_headers:
        return (*result, dict(next(m['headers'] for m in output if m['type'] == 'http.response.start')))
    return result


class RegressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.password = 'local-regression-password-123'
        cls.password_hash = PASSWORDS.hash(cls.password)


    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='lab-regression-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = Store(self.root / 'storage')
        self.store.initialize()
        self.public = Store(self.root / 'storage', public=True)
        self.user = User(id='admin1', displayName='Local Test', login='tester', passwordHash=self.password_hash).model_dump()
        self.store.change(lambda s: s['users'].append(copy.deepcopy(self.user)), resources=('users',))


    @contextmanager
    def env(self):
        with patch.dict(os.environ, {'LAB_STORAGE': str(self.store.root),
                                     'LAB_ADMIN_ORIGIN': 'http://127.0.0.1', 'LAB_DEV': 'true'}):
            yield


    def with_app(self, action):
        async def run():
            app = admin_app()
            async with app.router.lifespan_context(app):
                return await action(app)
        with self.env():
            return asyncio.run(run())


    def test_http_rejects_storage_capacity_before_committing(self):
        """小型HTTP請求也可能令整表超限；回422且仍可讀寫原資料。"""
        async def run(app):
            token, session = app.state.security.issue(self.user)
            before = self.store.read(include_audit=True)
            person = Person(id='oversized', familyName='Test', givenName='Person', bioMarkdown='X' * 5000).model_dump()
            with patch('lab_backend.store.MAX_FILE', 2048):
                status, _ = await request(app, '/api/v1/admin/content/people', method='POST',
                    token=token, csrf=session.csrf, body={'data': person})
                self.assertEqual(status, 422)
                self.assertEqual(self.store.read(include_audit=True), before)
                status, _ = await request(app, '/api/v1/admin/content/people', token=token)
                self.assertEqual(status, 200)
        self.with_app(run)


    def test_anonymous_protected_request_never_queries_database(self):
        async def run(app):
            with (patch.object(app.state.store, 'read', side_effect=AssertionError('must not read')),
                  patch.object(app.state.store, 'change', side_effect=AssertionError('must not write'))):
                status, _ = await request(app, '/api/v1/admin/content/people', method='POST', body={})
                self.assertEqual(status, 401)
        self.with_app(run)


    def test_bad_login_shape_does_not_write_audit(self):
        async def run(app):
            with patch.object(app.state.store, 'change', side_effect=AssertionError('must not write')):
                status, _ = await request(app, '/api/v1/auth/login', method='POST', body={})
                self.assertEqual(status, 422)
        self.with_app(run)


    def test_local_login_management_pages_and_logout(self):
        """本地帳密登入後可操作各頁 API；失敗與登出不能取得管理內容。"""
        async def run(app):
            routes = ['/api/v1/admin/schema', '/api/v1/admin/references', '/api/v1/admin/users', '/api/v1/admin/audit']
            routes += [f'/api/v1/admin/content/{key}' for key in ('people', 'publications', 'assets')]
            for path in routes:
                self.assertEqual((await request(app, path))[0], 401)
            credentials = {'login': 'tester', 'password': self.password}
            status, _, headers = await request(app, '/api/v1/auth/login', method='POST',
                body={**credentials, 'password': 'wrong-password'}, include_headers=True)
            self.assertEqual(status, 401)
            self.assertNotIn(b'set-cookie', headers)
            self.assertEqual((await request(app, '/api/v1/auth/login', method='POST',
                body={'login': 'tester'}))[0], 422)
            # 在真正的 Argon2 驗證後取得 Session，不替換密碼檢查函式。
            status, data, headers = await request(app, '/api/v1/auth/login', method='POST', body=credentials, include_headers=True)
            self.assertEqual(status, 200)
            cookies = SimpleCookie(); cookies.load(headers[b'set-cookie'].decode())
            cookie = cookies['lab_session_dev']; token = cookie.value
            self.assertTrue(cookie['httponly'])
            self.assertEqual(cookie['samesite'], 'strict')
            for key in ('passwordHash', 'totpSecret', 'lastTotpStep'):
                self.assertNotIn(key, data['user'])
            for path in routes:
                self.assertEqual((await request(app, path, token=token))[0], 200, path)
            self.assertEqual((await request(app, '/api/v1/admin/content/people', method='POST', token=token,
                body={'data': Person(familyName='Test', id='p1', givenName='New Member').model_dump()}))[0], 403)
            status, _ = await request(app, '/api/v1/admin/content/people', method='POST', token=token, csrf=data['csrf'],
                body={'data': Person(familyName='Test', id='p1', givenName='New Member').model_dump()})
            self.assertEqual(status, 201)
            self.assertEqual((await request(app, '/api/v1/auth/logout', method='POST', token=token, csrf=data['csrf']))[0], 200)
            self.assertEqual((await request(app, '/api/v1/auth/session', token=token))[0], 401)
            self.assertEqual((await request(app, '/api/v1/admin/content/people', token=token))[0], 401)
        self.with_app(run)


    def test_csrf_rejected_before_body_validation(self):
        async def run(app):
            token, _ = app.state.security.issue(self.user)
            before = self.store.read(include_audit=True)
            status, _ = await request(app, '/api/v1/admin/content/people', method='POST', body={}, token=token)
            self.assertEqual(status, 403)
            self.assertEqual(before, self.store.read(include_audit=True))
        self.with_app(run)


    def test_only_admin_account_roles_are_accepted(self):
        """不只隱藏選單；新增及修改 API 都拒絕 public 與已取消角色。"""
        async def run(app):
            token, session = app.state.security.issue(self.user)
            new_user = {'displayName': 'New Admin', 'login': 'new-admin', 'password': self.password}
            for role in ('editor', 'publisher', 'public'):
                with self.subTest(role=role):
                    status, _ = await request(app, '/api/v1/admin/users', method='POST',
                                              body={**new_user, 'role': role}, token=token, csrf=session.csrf)
                    self.assertEqual(status, 422)
                    status, _ = await request(app, '/api/v1/admin/users/admin1', method='PATCH',
                                              body={'role': role, 'active': True, 'version': 1}, token=token, csrf=session.csrf)
                    self.assertEqual(status, 422)
            self.assertEqual(self.store.read(('users',))['users'], [self.user])
            for path in ('status', 'login'):
                self.assertEqual((await request(app, f'/api/v1/auth/umpass/{path}', token=token))[0], 404)
            # 固定 admin 角色不代表可以停用最後一名本地管理員。
            status, _ = await request(app, '/api/v1/admin/users/admin1', method='PATCH',
                                      body={'active': False, 'version': 1}, token=token, csrf=session.csrf)
            self.assertEqual(status, 422)
            self.assertTrue(self.store.read(('users',))['users'][0]['active'])
        self.with_app(run)


    def test_password_only_user_creation_and_password_change(self):
        """帳戶直接建立、可用帳密登入；重複名稱拒絕，改密碼使舊登入失效。"""
        async def run(app):
            token, session = app.state.security.issue(self.user)
            auth = {'token': token, 'csrf': session.csrf}
            body = {'login': 'second', 'displayName': 'Second Admin', 'password': self.password}
            self.assertEqual((await request(app, '/api/v1/admin/users', method='POST', body=body))[0], 401)
            status, created = await request(app, '/api/v1/admin/users', method='POST', body=body, **auth)
            self.assertEqual(status, 201)
            self.assertNotIn('passwordHash', created)
            self.assertNotIn('totpSecret', User.model_fields)
            self.assertNotIn('authSource', User.model_fields)
            self.assertEqual((await request(app, '/api/v1/admin/users', method='POST', body=body, **auth))[0], 409)
            credentials = {'login': 'second', 'password': self.password}
            status, identity = await request(app, '/api/v1/auth/login', method='POST', body=credentials)
            self.assertEqual(status, 200)
            self.assertEqual(identity['user']['id'], created['id'])
            self.assertEqual((await request(app, '/api/v1/auth/password', method='POST',
                body={'currentPassword': 'incorrect', 'newPassword': 'different-password'}, **auth))[0], 400)
            self.assertEqual((await request(app, '/api/v1/auth/password', method='POST',
                body={'currentPassword': self.password, 'newPassword': 'different-password'}, **auth))[0], 200)
            self.assertEqual((await request(app, '/api/v1/auth/session', **auth))[0], 401)
            self.assertEqual((await request(app, '/api/v1/auth/login', method='POST',
                body={'login': 'tester', 'password': self.password}))[0], 401)
            self.assertEqual((await request(app, '/api/v1/auth/login', method='POST',
                body={'login': 'tester', 'password': 'different-password'}))[0], 200)
        self.with_app(run)


    def test_cli_bootstrap_and_password_recovery_without_mfa_files(self):
        """首位帳戶及密碼恢復只需帳密，不生成或要求任何 MFA 金鑰。"""
        store = Store(self.root / 'bootstrap')
        store.initialize()
        with patch('builtins.input', side_effect=['starter', 'Starter']), patch('lab_backend.cli.password_prompt', return_value=self.password):
            local_admin(store)
        original = store.read(('users',))['users'][0]
        self.assertTrue(PASSWORDS.verify(original['passwordHash'], self.password))
        with patch('builtins.input', side_effect=['starter', 'Renamed']), patch('lab_backend.cli.password_prompt', return_value='replacement-password'):
            local_admin(store, recover=True)
        changed = store.read(('users',))['users'][0]
        self.assertEqual(changed['id'], original['id'])
        self.assertEqual(changed['authVersion'], original['authVersion'] + 1)
        self.assertTrue(PASSWORDS.verify(changed['passwordHash'], 'replacement-password'))
        self.assertFalse(list(store.root.rglob('*.key')))


    def test_three_resources_round_trip(self):
        """三類新增／修改／刪除立即反映公開查詢；衍生欄位不回傳寫入。"""
        sample = sample_data()
        state = state_from_sample(sample); state['users'] = [self.user]; self.store.replace(state)
        async def run(app):
            token, session = app.state.security.issue(self.user)
            auth = {'token': token, 'csrf': session.csrf}
            for resource, model in MODELS.items():
                data = {**{k: v for k, v in sample[resource][0].items() if k in model.model_fields}, 'id': 'new-' + resource}
                status, row = await request(app, f'/api/v1/admin/content/{resource}', method='POST', body={'data': data}, **auth)
                self.assertEqual(status, 201)
                key = 'givenName' if resource == 'people' else 'metadata' if resource == 'assets' else 'title'
                data[key] = {**data[key], 'alt': 'Changed'} if resource == 'assets' else 'Changed'
                status, updated = await request(app, f'/api/v1/admin/content/{resource}/{data["id"]}', method='PUT', body={'data': data, 'version': row['version']}, **auth)
                self.assertEqual(status, 200)
                visible = self.public.read((resource,))[resource]
                self.assertEqual(next(r for r in visible if r['id'] == data['id'])[key], data[key])
                for version, expected in ((1, 409), (2, 200)):
                    self.assertEqual((await request(app, f'/api/v1/admin/content/{resource}/{data["id"]}?version={version}', method='DELETE', **auth))[0], expected)
        self.with_app(run)


    def test_session_issue_and_preflight_reject_non_admin(self):
        """模擬舊 Session 仍在，但最新帳戶已不具 admin，前置授權必須拒絕。"""
        async def run(app):
            token, _ = app.state.security.issue(self.user)
            for role in ('editor', 'publisher', 'public'):
                changed = {**self.user, 'role': role}
                with self.subTest(role=role):
                    with self.assertRaises(HTTPException) as error:
                        app.state.security.issue(changed)
                    self.assertEqual(error.exception.status_code, 403)
                    with (patch.object(app.state.store, 'read', return_value={'users': [changed]}),
                          patch.object(app.state.store, 'change', side_effect=AssertionError('must not write'))):
                        status, _ = await request(app, '/api/v1/admin/schema', token=token)
                        self.assertEqual(status, 403)
            with self.assertRaises(HTTPException):
                app.state.security.issue({**self.user, 'active': False})
        self.with_app(run)


    def test_revoked_session_rejected_by_preflight(self):
        async def run(app):
            token, session = app.state.security.issue(self.user)
            self.store.change(lambda s: s['users'][0].update(authVersion=2), resources=('users',))
            status, _ = await request(app, '/api/v1/admin/content/people', method='POST', body={}, token=token, csrf=session.csrf)
            self.assertEqual(status, 401)
        self.with_app(run)


    def test_credentials_changed_during_password_check_cannot_login(self):
        async def run(app):
            def verify(encoded, value):
                self.store.change(lambda s: s['users'][0].update(authVersion=2), resources=('users',))
                return True
            with patch('lab_backend.auth.verify_password', side_effect=verify):
                status, _ = await request(app, '/api/v1/auth/login', method='POST',
                                          body={'login': 'tester', 'password': self.password})
            self.assertEqual(status, 401)
            self.assertEqual(self.store.read_audit()['total'], 0)
        self.with_app(run)


    def test_five_tables_no_csv_runtime_and_readonly_public(self):
        """公開連線僅有內容庫，SQLite 檔案本身也用唯讀模式開啟。"""
        with self.store.connection() as conn:
            content = {r[0] for r in conn.execute("SELECT name FROM content.sqlite_schema WHERE type='table'")}
            private = {r[0] for r in conn.execute("SELECT name FROM main.sqlite_schema WHERE type='table'")}
            self.assertEqual(content, {'people', 'publications', 'assets'})
            self.assertEqual(private, {'users', 'audit'})
            self.assertEqual(conn.execute('PRAGMA main.journal_mode').fetchone()[0], 'delete')
            self.assertEqual(conn.execute('PRAGMA content.synchronous').fetchone()[0], 2)
        self.assertFalse(list(self.store.root.rglob('*.csv')))
        self.assertFalse((self.store.root / 'control').exists())
        for path in (self.store.content_path, self.store.admin_path):
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        with self.public.connection() as conn:
            self.assertEqual([r[1] for r in conn.execute('PRAGMA database_list')], ['main'])
        with self.assertRaises(StoreError):
            self.public.read(('users',))
        with self.assertRaises(StoreError):
            self.public.change(lambda s: None)
        with self.assertRaises(StoreError):
            with self.public.connection() as conn:
                conn.execute('PRAGMA query_only=OFF')
                conn.execute('DELETE FROM main.people')
        with self.assertRaises(StoreError):
            with self.public.connection() as conn:
                conn.execute('SELECT * FROM main.users')

    def test_content_save_is_immediately_public_and_has_no_state_field(self):
        """新增/修改不需發布；舊狀態欄位不得寫入，版本衝突仍拒絕。"""
        async def run(app):
            token, session = app.state.security.issue(self.user)
            person = Person(familyName='Test', id='p1', givenName='New Member').model_dump()
            status, row = await request(app, '/api/v1/admin/content/people', method='POST',
                                        body={'data': person}, token=token, csrf=session.csrf)
            self.assertEqual(status, 201)
            self.assertNotIn('publishState', row['data'])
            self.assertNotIn('publishedAt', row['data'])
            visible = self.public.read(('people',))['people'][0]
            self.assertEqual(visible['givenName'], 'New Member')
            self.assertNotIn('publishState', visible)
            self.assertNotIn('publishedAt', visible)
            self.assertNotIn('_version', visible)
            with self.store.connection() as conn:
                fields = [r[1] for r in conn.execute('PRAGMA content.table_info(people)')]
                self.assertNotIn('publishState', fields)
                self.assertNotIn('publishedAt', fields)
            row['data']['givenName'] = 'Updated Member'
            status, updated = await request(app, '/api/v1/admin/content/people/p1', method='PUT',
                body={'data': {k: v for k, v in row['data'].items() if k not in ('name', 'publicationIds')}, 'version': row['version']}, token=token, csrf=session.csrf)
            self.assertEqual(status, 200)
            self.assertEqual(self.public.read(('people',))['people'][0]['givenName'], 'Updated Member')
            status, _ = await request(app, '/api/v1/admin/content/people/p1', method='PUT',
                body={'data': {k: v for k, v in row['data'].items() if k not in ('name', 'publicationIds')}, 'version': row['version']}, token=token, csrf=session.csrf)
            self.assertEqual(status, 409)
            status, _ = await request(app, '/api/v1/admin/content/people/p1', method='PUT',
                body={'data': {**{k: v for k, v in updated['data'].items() if k not in ('name', 'publicationIds')}, 'publishState': 'draft'}, 'version': updated['version']}, token=token, csrf=session.csrf)
            self.assertEqual(status, 422)
        self.with_app(run)

    def test_v1_invalid_content_inputs_are_atomic(self):
        """格式錯誤422、版本衝突409可區分；錯誤不能改動資料或成功審計。"""
        async def run(app):
            token, session = app.state.security.issue(self.user)
            auth = {'token': token, 'csrf': session.csrf}
            base = '/api/v1/admin/content/people'
            person = {'id': 'p1', 'givenName': 'Member', 'familyName': 'Test'}
            status, created = await request(app, base, method='POST', body={'data': person}, **auth)
            self.assertEqual(status, 201)
            before = self.store.read(include_audit=True)
            for version in ({}, {'version': None}, {'version': 0}, {'version': True}, {'version': '1'}):
                status, error = await request(app, base + '/p1', method='PUT', body={'data': person, **version}, **auth)
                self.assertEqual(status, 422)
                self.assertIn('fields', error)
            for invalid in ({'createdAt': ''}, {'links': [{'label': 'Link', 'url': ''}]},
                            {'portrait': {'url': 'javascript:alert(1)'}},
                            {'portrait': {'url': '/assets/file.pdf', 'metadata': {'mimeType': 'application/pdf'}}},
                            {'_version': 1}, {'publishState': 'published'}, {'givenName': 123}):
                status, _ = await request(app, base + '/p1', method='PUT',
                                          body={'data': {**person, **invalid}, 'version': 1}, **auth)
                self.assertEqual(status, 422)
            # 型別聯集可辨識 Asset，但 resource=people 仍須拒絕錯誤的業務模型。
            status, _ = await request(app, base, method='POST',
                                      body={'data': {'id': 'asset1', 'url': '/assets/a.png'}}, **auth)
            self.assertEqual(status, 422)
            for path in ('unknown', 'research_areas', 'users'):
                self.assertEqual((await request(app, '/api/v1/admin/content/' + path, **auth))[0], 422)
            self.assertEqual((await request(app, base + '/p1', method='PUT',
                                           body={'data': person, 'version': 2}, **auth))[0], 409)
            self.assertEqual((await request(app, base, method='POST', body={'data': person}, **auth))[0], 409)
            self.assertEqual(self.store.read(include_audit=True), before)
            # 去掉未使用的slug後只以ID識別；不同ID可以同名，不增加姓名唯一限制。
            self.assertEqual((await request(app, base, method='POST',
                                           body={'data': {**person, 'id': 'p2'}}, **auth))[0], 201)
            status, error = await request(public_app(), '/api/v1/public/people/-invalid')
            self.assertEqual(status, 422)
            self.assertEqual(error['fields'], [['path', 'person_id']])
        self.with_app(run)

    def test_person_put_replaces_optional_values(self):
        """PUT省略照片會清空；反向清單及顯示姓名不能成為第二份來源。"""
        async def run(app):
            token, session = app.state.security.issue(self.user)
            auth = {'token': token, 'csrf': session.csrf}
            data = {'id': 'p1', 'familyName': 'Chen', 'givenName': 'Amy'}
            _, row = await request(app, '/api/v1/admin/content/people', method='POST', body={'data': {**data, 'portrait': {'url': '/assets/p.jpg'}}}, **auth)
            for field, value in [('name', 'Wrong'), ('publicationIds', []), ('isDemo', False), ('portraitAssetId', 'photo'),
                                 ('slug', 'old-alias'), ('sourceUrl', 'https://example.invalid/profile'), ('affiliation', 'Unused')]:
                self.assertEqual((await request(app, '/api/v1/admin/content/people/p1', method='PUT', body={'data': {**data, field: value}, 'version': 1}, **auth))[0], 422)
            status, updated = await request(app, '/api/v1/admin/content/people/p1', method='PUT', body={'data': data, 'version': 1}, **auth)
            self.assertEqual(status, 200)
            self.assertIsNone(updated['data']['portrait'])
            self.assertEqual(updated['created'], row['created'])
            self.assertEqual(updated['data']['name'], 'Amy Chen')
        self.with_app(run)


    def test_database_dictionary_matches_physical_sqlite(self):
        """以實際建庫的 PRAGMA 核對文件，不只比對 Python 模型的名稱。"""
        text = (ROOT / 'docs/DATABASE.md').read_text()
        count = 0
        with self.store.connection() as conn:
            for resource, table in TABLES.items():
                schema = 'content' if resource in MODELS else 'main'
                section = re.search(rf'^### {table}\n(.*?)(?=^##|\Z)', text, re.S | re.M).group(1)
                documented = re.findall(r'^\| `(\w+)` \| ([^|]+) \| ([^|]+) \|', section, re.M)
                actual = conn.execute(f'PRAGMA {schema}.table_info("{table}")').fetchall()
                self.assertEqual([row['name'] for row in actual], [row[0] for row in documented])
                for row, (_, sql_type, nullable) in zip(actual, documented):
                    self.assertEqual(row['type'], re.match(r'[A-Z]+', sql_type).group())
                    self.assertIsNone(row['dflt_value'])
                    self.assertEqual(bool(row['pk']), 'PK' in sql_type)
                    if row['name'] != '_singleton':
                        self.assertEqual(bool(row['notnull']), nullable == '否')
                self.assertEqual(conn.execute(f'PRAGMA {schema}.foreign_key_list("{table}")').fetchall(), [])
                indexes = conn.execute(f'PRAGMA {schema}.index_list("{table}")').fetchall()
                unique = {tuple(r['name'] for r in conn.execute(f'PRAGMA {schema}.index_info("{index["name"]}")'))
                          for index in indexes if index['unique']}
                expected = {(key,) for key, sql_type, _ in documented if ('PK' in sql_type or 'UNIQUE' in sql_type) and key != '_singleton'}
                self.assertEqual(unique, expected)
                count += len(actual)
        self.assertEqual(count, 46)

    def test_merged_history_is_server_owned_and_not_public(self):
        """管理歷史保留建立者；公開頁面不再需要時間或操作者。"""
        second = {**self.user, 'id': 'admin2', 'login': 'second'}
        self.store.change(lambda state: state['users'].append(second), resources=('users',))
        async def run(app):
            token, session = app.state.security.issue(self.user)
            other_token, other_session = app.state.security.issue(second)
            auth = {'token': token, 'csrf': session.csrf}
            other = {'token': other_token, 'csrf': other_session.csrf}
            samples = {
                'assets': {'id': 'x', 'url': '/assets/photo.jpg', 'metadata': {'purpose': 'group_photo'}},
                'people': {'id': 'x', 'familyName': 'Chen', 'givenName': 'Amy'},
                'publications': {'id': 'x', 'title': 'Paper', 'year': 2026, 'authors': [{'name': 'Author'}]},
            }
            for resource, data in samples.items():
                path = f'/api/v1/admin/content/{resource}'
                with patch('lab_backend.models.now', return_value='2026-11-06T10:00:00+00:00'):
                    status, created = await request(app, path, method='POST', body={'data': data}, **auth)
                self.assertEqual(status, 201)
                self.assertEqual(created['created'], {'at': '2026-11-06T10:00:00+00:00', 'by': 'admin1'})
                self.assertEqual(created['updated'], created['created'])
                self.assertEqual(set(created), {'data', 'version', 'created', 'updated'})
                before = self.store.read(include_audit=True)
                # 無論藏在data或外層，新／舊歷史欄位都不可由瀏覽器指定。
                for key, value in [('created', created['created']), ('updated', created['updated']),
                                   ('createdAt', '2026-01-01T00:00:00Z'), ('updatedAt', '2026-01-01T00:00:00Z'),
                                   ('createdBy', 'forged'), ('_createdBy', 'forged'), ('_updatedBy', 'forged')]:
                    for body in ({'data': {**data, key: value}, 'version': 1},
                                 {'data': data, 'version': 1, key: value}):
                        self.assertEqual((await request(app, path + '/x', method='PUT', body=body, **other))[0], 422)
                self.assertEqual(self.store.read(include_audit=True), before)
                with patch('lab_backend.content.now', return_value='2026-11-06T11:00:00+00:00'):
                    status, changed = await request(app, path + '/x', method='PUT', body={'data': data, 'version': 1}, **other)
                self.assertEqual(status, 200)
                self.assertEqual(changed['created'], created['created'])
                self.assertEqual(changed['updated'], {'at': '2026-11-06T11:00:00+00:00', 'by': 'admin2'})
                self.assertEqual(changed['version'], 2)
                if resource == 'assets':
                    exposed = (await request(public_app(), '/api/v1/public/home'))[1]['assets'][0]
                elif resource == 'people':
                    exposed = (await request(public_app(), '/api/v1/public/people/x'))[1]['person']
                else:
                    exposed = (await request(public_app(), '/api/v1/public/publications'))[1][0]
                self.assertFalse({'created', 'updated', 'createdBy', '_createdBy', '_updatedBy'} & set(exposed))
                self.assertNotIn('admin1', json.dumps(exposed))
                self.assertNotIn('admin2', json.dumps(exposed))
                if resource != 'assets':
                    self.assertNotIn('createdAt', exposed)
                    self.assertNotIn('updatedAt', exposed)
                    self.assertNotIn('publishedAt', exposed)
        self.with_app(run)

    def test_merged_history_invalid_shape_is_rejected(self):
        """備份／SQL讀取的歷史同樣驗證at/by，不能只驗證一般內容欄位。"""
        self.store.change(lambda state: state['assets'].append({
            **Asset(id='x', url='/assets/x.png').model_dump(), **new_metadata()}))
        before = self.store.read(include_audit=True)
        for invalid in ({'at': '2026-11-06T10:00:00Z'}, {'at': '2026-11-06T10:00:00', 'by': 'system'},
                        {'at': '2026-11-06T10:00:00Z', 'by': ''}, 'not-an-object'):
            with self.assertRaises(ValueError):
                self.store.change(lambda state: state['assets'][0].update(created=invalid))
            self.assertEqual(self.store.read(include_audit=True), before)
        with self.store.connection(write=True) as conn:
            conn.execute('UPDATE content.assets SET updated=? WHERE id=?', ('{"at":"invalid","by":"private-actor"}', 'x'))
        with self.assertRaises(StoreError):
            self.public.read(('assets',))

    def test_public_page_contracts_and_no_private_routes(self):
        """首頁僅三篇；列表有簡介，詳情同快照帶論文；舊路由與私有欄位不公開。"""
        def content(state):
            state['people'] = [{**Person(familyName='Test', id=i, givenName=i, bioMarkdown='Intro').model_dump(), **new_metadata()} for i in ('p1', 'p2')]
            state['assets'] = [{**Asset(id=i, url=f'/assets/{i}.png', metadata={'purpose': purpose}).model_dump(), **new_metadata()}
                               for i, purpose in [('asset1', 'group_photo'), ('unlisted-photo', 'group_photo'), ('logo', 'other')]]
            state['publications'] = [{**Publication(id=f'paper{i}', title='Paper', year=2026-i,
                pdfPath=f'/assets/papers/paper{i}.pdf', authors=[Author(personId='p1', name='First'), Author(name='External')]).model_dump(), **new_metadata()} for i in (2, 1, 3, 4)]
        self.store.change(content)
        async def run():
            app = public_app()
            async with app.router.lifespan_context(app):
                status, home, headers = await request(app, '/api/v1/public/home', include_headers=True)
                self.assertEqual(status, 200)
                Home.model_validate(home)
                self.assertEqual(set(home), set(Home.model_fields))
                self.assertEqual(headers[b'cache-control'], b'no-store')
                self.assertNotIn(b'set-cookie', headers)
                self.assertEqual(home['counts'], {'publications': 4})
                self.assertEqual([p['id'] for p in home['recentPublications']], ['paper4', 'paper3', 'paper1'])
                self.assertEqual([a['id'] for a in home['assets']], ['asset1', 'unlisted-photo'])
                self.assertEqual(set(home['recentPublications'][0]), {'id', 'title', 'year', 'type', 'venue'})
                _, summaries = await request(app, '/api/v1/public/people')
                self.assertEqual([p['id'] for p in summaries], ['p1', 'p2'])
                self.assertEqual(summaries[0]['bioMarkdown'], 'Intro')
                self.assertNotIn('publicationIds', summaries[0])
                self.assertNotIn('publicEmail', summaries[0])
                self.assertEqual(set(summaries[0]), {'id', 'familyName', 'givenName', 'englishName', 'name', 'title', 'portrait', 'bioMarkdown', 'memberships'})
                _, detail = await request(app, '/api/v1/public/people/p1')
                self.assertEqual([p['id'] for p in detail['publications']], ['paper4', 'paper3', 'paper1', 'paper2'])
                self.assertEqual(set(detail['person']), set(summaries[0]) | {'interests', 'publicEmail', 'links'})
                self.assertEqual(detail['publications'], (await request(app, '/api/v1/public/publications'))[1])
                self.assertEqual(set(detail['publications'][0]), {'id', 'title', 'year', 'type', 'venue', 'authors', 'pdfPath', 'sourceUrl'})
                self.assertEqual(detail['publications'][0]['pdfPath'], '/assets/papers/paper4.pdf')
                self.assertEqual((await request(app, '/api/v1/public/people/p2'))[1]['publications'], [])
                self.assertEqual(await request(app, '/api/v1/public/people/missing'), (404, {'detail': '找不到成員'}))
                self.store.change(lambda s: s['publications'][-1].update(title='Changed'))
                self.assertEqual((await request(app, '/api/v1/public/home'))[1]['recentPublications'][0]['title'], 'Changed')
                self.assertEqual((await request(app, '/api/v1/admin/users'))[0], 404)
                self.assertEqual((await request(app, '/api/v1/auth/session'))[0], 404)
                self.assertEqual((await request(app, '/api/v1/public/home', method='POST', body={}))[0], 405)
        with self.env():
            asyncio.run(run())

    def test_people_directory_does_not_read_publications(self):
        """已啟動後即使論文內容損壞，成員列表仍只讀people；需要論文的頁面照常拒絕。"""
        self.store.change(lambda state: state.update(state_from_sample(sample_data())))
        async def run():
            app = public_app()
            async with app.router.lifespan_context(app):
                # 模擬服務啟動後資料檔遭外部損壞，不能藉此讓無效論文進入回應。
                with closing(sqlite3.connect(self.store.content_path)) as conn, conn:
                    conn.execute('UPDATE publications SET authors=?', ('invalid-json',))
                status, people = await request(app, '/api/v1/public/people')
                self.assertEqual(status, 200)
                self.assertEqual(len(people), 3)
                for path in ('/home', '/publications', '/people/person0'):
                    self.assertEqual((await request(app, '/api/v1/public' + path))[0], 503)
        with self.env():
            asyncio.run(run())

    def test_english_name_and_asset_purpose_contract(self):
        """英文名可空且不影響排序；舊欄位拒絕，CSV與管理頁使用同一新模型。"""
        async def run(app):
            token, session = app.state.security.issue(self.user)
            auth = {'token': token, 'csrf': session.csrf}
            path = '/api/v1/admin/content/people'
            data = {'id': 'person', 'familyName': 'Example', 'givenName': 'An', 'englishName': 'Avery'}
            status, row = await request(app, path, method='POST', body={'data': data}, **auth)
            self.assertEqual(status, 201)
            self.assertEqual(row['data']['englishName'], 'Avery')
            self.assertEqual(decode_csv('people', encode_csv('people', [data_only('people', row['data'])], private=False), private=False)[0]['englishName'], 'Avery')
            status, _ = await request(app, path, method='POST', body={'data': {**data, 'id': 'old', 'preferredName': 'Old'}}, **auth)
            self.assertEqual(status, 422)
            for version, value in enumerate([None, '', 'English Name'], 1):
                status, row = await request(app, path + '/person', method='PUT', body={'data': {**data, 'englishName': value}, 'version': version}, **auth)
                self.assertEqual(status, 200)
                person = (await request(public_app(), '/api/v1/public/people/person'))[1]['person']
                self.assertEqual(person['englishName'], value)
                self.assertEqual(person['name'], 'An Example')
            invalid = {'id': 'bad-photo', 'url': '/assets/paper.pdf', 'metadata': {'purpose': 'group_photo', 'mimeType': 'application/pdf'}}
            self.assertEqual((await request(app, '/api/v1/admin/content/assets', method='POST', body={'data': invalid}, **auth))[0], 422)
            self.assertEqual(self.store.read(('assets',))['assets'], [])
        self.with_app(run)

    def test_all_remaining_endpoints_and_removed_resources(self):
        """四個頁面每次查庫，移除端點不會以靜態值回應。"""
        async def run(app):
            token, session = app.state.security.issue(self.user)
            public = public_app()
            for resource in ('people', 'publications'):
                self.assertEqual(await request(public, f'/api/v1/public/{resource}'), (200, []))
            self.assertEqual(await request(public, '/api/v1/public/home'), (200, {
                'assets': [], 'recentPublications': [], 'counts': {'publications': 0}}))
            for resource in ('lab', 'assets', 'people/p1/publications', 'publications/paper1', 'researchAreas', 'opportunities', 'siteSettings', 'news', 'schemaVersion'):
                self.assertEqual((await request(public, f'/api/v1/public/{resource}'))[0], 404)
            for resource in ('researchAreas', 'opportunities', 'siteSettings', 'news', 'schemaVersion'):
                self.assertEqual((await request(app, f'/api/v1/admin/content/{resource}', token=token))[0], 422)
            with patch.object(public.state.store, 'read', side_effect=StoreError('private path')):
                for resource in ('home', 'people', 'people/p1', 'publications'):
                    status, data = await request(public, f'/api/v1/public/{resource}')
                    self.assertEqual(status, 503)
                    self.assertNotIn('private path', str(data))
        self.with_app(run)

    def test_asset_metadata_and_pdf_api_and_csv(self):
        """管理可改描述／PDF，非法路徑整筆拒絕，CSV往返不丟失新欄位。"""
        async def run(app):
            token, session = app.state.security.issue(self.user)
            auth = {'token': token, 'csrf': session.csrf}
            base = '/api/v1/admin/content/'
            asset = {'id': 'asset', 'url': '/assets/image.jpg', 'metadata': {'mimeType': 'image/jpeg', 'alt': 'Picture', 'caption': 'Caption', 'credit': 'Lab', 'purpose': 'other'}}
            status, created = await request(app, base + 'assets', method='POST', body={'data': asset}, **auth)
            self.assertEqual(status, 201)
            self.assertEqual(created['data'], asset)
            self.assertEqual((await request(app, '/api/v1/admin/references', **auth))[1]['assets'][0]['label'], 'Picture')
            before = self.store.read(include_audit=True)
            for bad in ({**asset, 'width': 10}, {**asset, 'height': None}, {**asset, 'alt': 'Old format'},
                        {**asset, 'metadata': {**asset['metadata'], 'height': None}}):
                self.assertEqual((await request(app, base + 'assets/asset', method='PUT', body={'data': bad, 'version': 1}, **auth))[0], 422)
            self.assertEqual(self.store.read(include_audit=True), before)
            data = Publication(id='paper', title='Paper', year=2026,
                               authors=[Author(name='External')], pdfPath='/assets/papers/paper-1.pdf').model_dump()
            status, paper = await request(app, base + 'publications', method='POST', body={'data': data}, **auth)
            self.assertEqual(status, 201)
            self.assertEqual((await request(app, base + 'publications/paper', method='PUT',
                body={'data': {**data, 'slug': 'old-paper'}, 'version': 1}, **auth))[0], 422)
            self.assertEqual((await request(public_app(), '/api/v1/public/publications'))[1][0]['pdfPath'], data['pdfPath'])
            before = self.store.read(include_audit=True)
            for path in ('', '/etc/passwd', 'https://example.org/p.pdf', '//example.org/p.pdf', '/assets/../secret.pdf',
                         '/assets/%2e%2e/p.pdf', '/assets/p.pdf?download=1', '/assets/p.pdf#x', '/assets/p.png', '/assets/p.pdf\n'):
                self.assertEqual((await request(app, base + 'publications/paper', method='PUT', body={'data': {**data, 'pdfPath': path}, 'version': 1}, **auth))[0], 422)
            self.assertEqual(self.store.read(include_audit=True), before)
            self.assertEqual((await request(app, base + 'publications/paper', method='PUT', body={'data': {**data, 'pdfPath': None}, 'version': 1}, **auth))[0], 200)
            self.assertIsNone(self.public.read(('publications',))['publications'][0]['pdfPath'])
            # 別的測試覆蓋備份；這裡確認新增業務欄位也進入一般內容CSV。
            folder = self.root / 'new-fields'; folder.mkdir()
            for resource, row in [('assets', {**asset, 'id': 'csv-asset'}),
                                  ('publications', {**data, 'id': 'csv-paper'})]:
                text = encode_csv(resource, [MODELS[resource].model_validate(row).model_dump()], private=False)
                self.assertEqual(decode_csv(resource, text, private=False)[0], row)
                (folder / FILES[resource]).write_text(text)
            return folder
        folder = self.with_app(run)
        import_csv(self.store, folder, apply=True)
        self.assertEqual(self.public.read(('assets',))['assets'][1]['metadata']['alt'], 'Picture')
        self.assertEqual(self.public.read(('publications',))['publications'][0]['pdfPath'], '/assets/papers/paper-1.pdf')

    def test_sql_parameter_binding_and_reference_validation(self):
        """文字中的 SQL 不可被執行；作者關聯與不可刪除引用仍由交易驗證。"""
        person = {**Person(familyName='Test', id='p1', givenName="Robert'); DROP TABLE people;--").model_dump(), **new_metadata()}
        self.store.change(lambda s: s['people'].append(person))
        self.assertEqual(self.public.read(('people',))['people'][0]['givenName'], person['givenName'])
        paper = {**Publication(id='paper1', title='Paper', year=2026,
                               authors=[Author(personId='p1', name='Author')]).model_dump(), **new_metadata()}
        self.store.change(lambda s: s['publications'].append(paper))
        with self.assertRaises(ValueError):
            self.store.change(lambda s: s['people'].clear())
        self.assertEqual(len(self.public.read(('people',))['people']), 1)
        with self.assertRaises(StoreError):
            self.public.read(('people; DROP TABLE people',))

    def test_cross_database_transaction_rolls_back_content_user_and_audit(self):
        """在內容/帳戶 SQL 已寫入後注入失敗，兩庫均不可留下半次修改。"""
        before = self.store.read(include_audit=True)
        original = self.store.insert_rows
        def fail(conn, resource, rows, **kwargs):
            if resource == 'audit':
                raise RuntimeError('injected before audit insert')
            return original(conn, resource, rows, **kwargs)
        def operation(s):
            s['people'].append({**Person(familyName='Test', id='p1', givenName='Uncommitted').model_dump(), **new_metadata()})
            s['users'][0]['version'] += 1
            audit(s, 'admin1', 'must.rollback')
        with patch.object(self.store, 'insert_rows', side_effect=fail), self.assertRaises(RuntimeError):
            self.store.change(operation)
        self.assertEqual(self.store.read(include_audit=True), before)
        self.assertEqual(self.public.read(('people',))['people'], [])

    def test_corrupt_nested_content_fails_closed_without_leaking_raw_input(self):
        """外部損壞的 JSON 也必須回泛化 503，不能當成有效內容或回傳原始值。"""
        self.store.change(lambda s: s['people'].append({**Person(familyName='Test', id='p1', givenName='Member').model_dump(), **new_metadata()}))
        with closing(sqlite3.connect(self.store.content_path)) as conn, conn:
            conn.execute('UPDATE people SET memberships=? WHERE id=?', ('not-json-sensitive-value', 'p1'))
        with self.assertRaises(StoreError):
            self.public.read(('people',))
        async def run():
            app = public_app()
            status, body = await request(app, '/api/v1/public/people')
            self.assertEqual(status, 503)
            self.assertNotIn('not-json-sensitive-value', str(body))
        with self.env():
            asyncio.run(run())

    def test_writer_serialization_preserves_version_conflicts(self):
        self.store.change(lambda s: s['people'].append({**Person(familyName='Test', id='p1', givenName='Original').model_dump(), **new_metadata()}))
        barrier = threading.Barrier(2)
        def edit(name):
            def operation(s):
                row = s['people'][0]
                if row['_version'] != 1:
                    raise HTTPException(409, 'conflict')
                row.update(givenName=name, _version=2)
                audit(s, 'admin1', 'edit', name)
            barrier.wait()
            try:
                self.store.change(operation)
                return 200
            except HTTPException as exc:
                return exc.status_code
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(edit, ('One', 'Two')))
        self.assertEqual(sorted(results), [200, 409])
        self.assertEqual(self.store.read_audit()['total'], 1)

    def test_users_read_does_not_attach_content_database(self):
        real = sqlite3.connect
        statements = []
        def traced(*args, **kwargs):
            conn = real(*args, **kwargs)
            conn.set_trace_callback(statements.append)
            return conn
        with patch('lab_backend.store.sqlite3.connect', side_effect=traced):
            self.store.read(('users',))
        self.assertFalse(any('ATTACH' in statement for statement in statements))
        self.assertFalse(any('audit' in statement.lower() for statement in statements))

    def test_read_transaction_stays_consistent_and_new_request_sees_commit(self):
        self.store.change(lambda s: s['people'].append({**Person(familyName='Test', id='p1', givenName='Old').model_dump(), **new_metadata()}))
        with ThreadPoolExecutor(max_workers=2) as pool:
            with self.public.connection() as conn:
                self.assertEqual(self.public.read_rows(conn, 'people')[0]['givenName'], 'Old')
                # 第二個讀者不需要等第一個讀者釋放連線。
                self.assertIn('assets', pool.submit(self.public.read, ('assets',)).result(timeout=2))
                future = pool.submit(self.store.change, lambda s: s['people'][0].update(givenName='New'))
                self.assertEqual(self.public.read_rows(conn, 'people')[0]['givenName'], 'Old')
            future.result(timeout=5)
        self.assertEqual(self.public.read(('people',))['people'][0]['givenName'], 'New')

    def test_wal_mode_is_rejected_for_atomic_two_database_storage(self):
        with closing(sqlite3.connect(self.store.content_path)) as conn, conn:
            conn.execute('PRAGMA journal_mode=WAL')
        with self.assertRaises(StoreError):
            self.public.read()
        with self.assertRaises(StoreError):
            self.store.change(lambda s: audit(s, 'admin1', 'must.not.commit'))
        with closing(sqlite3.connect(self.store.content_path)) as conn, conn:
            conn.execute('PRAGMA journal_mode=DELETE')
        self.assertEqual(self.store.read_audit()['total'], 0)

    def test_public_refuses_journal_until_admin_recovers(self):
        """模擬非 hot 的殘留日誌；公開入口不能推斷私有 super-journal 已不存在。"""
        journal = self.store.content_path.with_name('content.db-journal')
        # 這只驗拒絕/清理邏輯，並不是跨庫斷電或真正 hot journal 恢復驗收。
        journal.write_bytes(b'\x00' * 512)
        with self.assertRaises(StoreError):
            self.public.read()
        self.store.recover_journals()
        self.assertFalse(journal.exists())
        self.assertIn('assets', self.public.read())

    def test_password_verification_outside_database_transaction(self):
        async def run(app):
            def verify(encoded, value):
                with self.store.connection(write=True):
                    self.assertIn('assets', self.public.read(('assets',)))
                return True
            with patch('lab_backend.auth.verify_password', side_effect=verify):
                status, _ = await request(app, '/api/v1/auth/login', method='POST',
                    body={'login': 'tester', 'password': self.password})
            self.assertEqual(status, 200)
        self.with_app(run)

    def csv_batch(self, people):
        """僅建立暫存匯入資料，不操作工作區 storage。"""
        directory = self.root / 'batch'
        directory.mkdir(exist_ok=True)
        (directory / 'people.csv').write_text(encode_csv('people', people, private=False))
        return directory

    def test_csv_preview_then_apply_is_public_and_skips_existing_ids(self):
        old = Person(familyName='Test', id='p1', givenName='Keep Original').model_dump()
        self.store.change(lambda s: s['people'].append({**old, **new_metadata()}))
        directory = self.csv_batch([{**old, 'givenName': 'Must Not Replace'}, Person(familyName='Test', id='p2', givenName='New Member').model_dump()])
        before = self.store.read(include_audit=True)
        import_csv(self.store, directory)
        self.assertEqual(self.store.read(include_audit=True), before)
        import_csv(self.store, directory, apply=True)
        people = self.public.read(('people',))['people']
        self.assertEqual([p['givenName'] for p in people], ['Keep Original', 'New Member'])
        self.assertNotIn('publishState', people[1])
        self.assertEqual(self.store.read_audit()['items'][0]['action'], 'content.import')

    def test_csv_invalid_batch_is_atomic_and_respects_maintenance_lock(self):
        directory = self.csv_batch([Person(familyName='Test', id='p1', givenName='Valid').model_dump()])
        paper = Publication(id='paper', title='Bad Link', year=2026,
                            authors=[Author(personId='missing', name='Missing')]).model_dump()
        (directory / 'publications.csv').write_text(encode_csv('publications', [paper], private=False))
        before = self.store.read(include_audit=True)
        with self.assertRaises(ValueError):
            import_csv(self.store, directory, apply=True)
        self.assertEqual(self.store.read(include_audit=True), before)
        with self.store.maintenance(), self.assertRaises(StoreError):
            import_csv(self.store, directory)
        (directory / 'publications.csv').unlink()
        (directory / 'users.csv').write_text('forbidden')
        with self.assertRaises(ValueError):
            import_csv(self.store, directory, apply=True)
        self.assertEqual(self.store.read(include_audit=True), before)

    def test_csv_duplicate_id_and_old_state_columns_rejected(self):
        person = Person(familyName='Test', id='p1', givenName='Member').model_dump()
        directory = self.csv_batch([person, person])
        with self.assertRaises(ValueError):
            import_csv(self.store, directory, apply=True)
        (directory / 'people.csv').write_text('id,publishState\np1,draft\n')
        with self.assertRaises(ValueError):
            import_csv(self.store, directory, apply=True)
        self.assertEqual(self.public.read(('people',))['people'], [])
        self.assertEqual(self.store.read_audit()['total'], 0)

    def test_people_and_publications_can_exceed_planning_estimates(self):
        """30 人／300 篇不是上限，CSV、API 新增修改與公開讀取都可超出估算。"""
        people = [Person(familyName='Test', id=f'p{i}', givenName='Member').model_dump() for i in range(30)]
        people[0]['memberships'] = [{'role': 'Alumni', 'isCurrent': False}]
        paper = Publication(id='paper', title='Paper', year=2026, authors=[Author(personId='p0', name='Member')]).model_dump()
        directory = self.csv_batch(people)
        (directory / 'publications.csv').write_text(encode_csv('publications',
            [{**paper, 'id': f'paper{i}'} for i in range(300)], private=False))
        import_csv(self.store, directory, apply=True)
        async def run(app):
            token, session = app.state.security.issue(self.user)
            for resource, data, field in (
                ('people', Person(familyName='Test', id='p30', givenName='Thirty-first').model_dump(), 'givenName'),
                ('publications', {**paper, 'id': 'paper300'}, 'title'),
            ):
                status, row = await request(app, f'/api/v1/admin/content/{resource}', method='POST',
                    body={'data': data}, token=token, csrf=session.csrf)
                self.assertEqual(status, 201)
                row['data'][field] = 'Updated beyond estimate'
                self.assertEqual((await request(app, f'/api/v1/admin/content/{resource}/{data["id"]}', method='PUT',
                    body={'data': {k: v for k, v in row['data'].items() if k not in ('name', 'publicationIds')}, 'version': row['version']}, token=token, csrf=session.csrf))[0], 200)
        self.with_app(run)
        visible = self.public.read()
        self.assertEqual(len(visible['people']), 31)
        self.assertEqual(len(visible['publications']), 301)

    def test_seed_csv_and_sqlite_do_not_impose_a_hidden_thousand_row_limit(self):
        """移除業務上限也需排除舊通用 1000 列限制；仍使用小記錄，非壓力測試。"""
        sample = sample_data()
        for resource in ('people', 'publications'):
            sample[resource].extend({**sample[resource][0], 'id': f'extra{i}'}
                                    for i in range(1001 - len(sample[resource])))
        state = state_from_sample(sample)
        store = Store(self.root / 'beyond-estimate')
        store.initialize(state)
        public = Store(store.root, public=True)
        for resource in ('people', 'publications'):
            self.assertEqual(len(public.read((resource,))[resource]), 1001)
        # CSV 超過 1000 列也可完整驗證並提交，不能在交換層另設總筆數上限。
        directory = self.csv_batch([{k: r[k] for k in MODELS['people'].model_fields} for r in state['people']])
        (directory / 'publications.csv').write_text(encode_csv('publications',
            [{k: r[k] for k in MODELS['publications'].model_fields} for r in state['publications']], private=False))
        for resource in ('assets',):
            (directory / FILES[resource]).write_text(encode_csv(resource,
                [{k: r[k] for k in MODELS[resource].model_fields} for r in state[resource]], private=False))
        import_csv(self.store, directory, apply=True)
        self.assertEqual(len(self.public.read(('people',))['people']), 1001)
        self.assertEqual(len(self.public.read(('publications',))['publications']), 1001)

    def test_audit_paging_stream_export_and_export_failure_no_delete(self):
        self.store.change(lambda s: [audit(s, 'admin1', 'event', str(i)) for i in range(11)], resources=())
        self.assertEqual([r['target'] for r in self.store.read_audit(2, 4)['items']], ['8', '7', '6', '5'])
        output = self.root / 'audit.csv'
        with patch('lab_backend.store.MAX_FILE', 1):
            self.store.export_audit(output)  # 串流匯出不受整批讀取大小限制。
        self.assertEqual(len(decode_csv('audit', output.read_text())), 11)
        with self.assertRaises(FileExistsError):
            self.store.export_audit(output, cutoff=datetime.now(timezone.utc) + timedelta(seconds=1))
        self.assertEqual(self.store.read_audit()['total'], 11)
        self.store.export_audit(self.root / 'archive.csv', cutoff=datetime.now(timezone.utc) + timedelta(seconds=1))
        self.assertEqual(self.store.read_audit()['total'], 1)
        self.assertEqual(self.store.read_audit()['items'][0]['action'], 'audit.archived')

    def test_encrypted_backup_restore_preview_and_wrong_password(self):
        self.store.change(lambda s: s['people'].append({**Person(familyName='Test', id='p1', givenName='Backup Member').model_dump(), **new_metadata()}))
        self.store.change(lambda s: audit(s, 'admin1', 'backup.test'), resources=())
        archive = self.root / 'backup.enc'
        with self.env(), patch('lab_backend.cli.password_prompt', return_value=self.password):
            backup(self.store, archive)
        other = Store(self.root / 'restored')
        other.initialize()
        before = other.read(include_audit=True)
        with patch('getpass.getpass', return_value='incorrect'), self.assertRaises(InvalidToken):
            restore(other, archive, apply=True)
        self.assertEqual(other.read(include_audit=True), before)
        with patch('getpass.getpass', return_value=self.password):
            restore(other, archive)
            self.assertEqual(other.read(include_audit=True), before)
            restore(other, archive, apply=True)
        user = other.read(('users',))['users'][0]
        self.assertTrue(PASSWORDS.verify(user['passwordHash'], self.password))
        self.assertEqual(user['authVersion'], self.user['authVersion'] + 1)
        self.assertEqual(other.read_audit()['total'], 2)
        self.assertEqual(Store(other.root, public=True).read(('people',))['people'][0]['givenName'], 'Backup Member')
        for key in ('created', 'updated'):
            self.assertEqual(other.read(('people',))['people'][0][key], self.store.read(('people',))['people'][0][key])

    def test_initialize_never_overwrites_existing_database_or_legacy_directory(self):
        before = self.store.read(include_audit=True)
        with self.assertRaises(StoreError):
            self.store.initialize()
        self.assertEqual(self.store.read(include_audit=True), before)
        legacy = self.root / 'legacy'
        legacy.mkdir()
        (legacy / 'CURRENT').write_text('legacy')
        with self.assertRaises(StoreError):
            Store(legacy).initialize()
        self.assertFalse((legacy / 'content').exists())

    def test_previous_database_and_backup_formats_are_not_silently_reused(self):
        """欄位已不相容；舊庫只報錯，不自動刪表或冒充新格式。"""
        with closing(sqlite3.connect(self.store.content_path)) as conn:
            conn.execute('PRAGMA user_version=6')
        before = self.store.content_path.read_bytes()
        with self.assertRaises(StoreError):
            self.public.read()
        self.assertEqual(self.store.content_path.read_bytes(), before)
        archive = self.root / 'old.enc'
        archive.write_bytes(b'LABSQL6\x00legacy-format')
        with self.assertRaises(ValueError):
            read_backup(archive, self.password)

    def test_empty_init_without_reference_and_csv_import(self):
        """初始化完全不需要參考JSON，合成CSV匯入後才有內容。"""
        store = Store(self.root / 'empty-init')
        with patch('lab_backend.cli.read_file', side_effect=AssertionError('init must not read reference data')):
            init(store)
        self.assertTrue(all(not rows for rows in store.read().values()))
        sample = sample_data()
        directory = self.root / 'batch'; directory.mkdir()
        for resource, rows in sample.items():
            (directory / FILES[resource]).write_text(encode_csv(resource, rows, private=False))
        import_csv(store, directory, apply=True)
        async def run():
            app = public_app()
            async with app.router.lifespan_context(app):
                self.assertEqual((await request(app, '/api/v1/public/home'))[1]['counts'], {'publications': 3})
                self.assertEqual([p['id'] for p in (await request(app, '/api/v1/public/publications'))[1]], ['paper2', 'paper1', 'paper0'])
                store.change(lambda state: state['publications'][0].update(year=2100, title='Edited oldest'))
                self.assertEqual([p['id'] for p in (await request(app, '/api/v1/public/publications'))[1]], ['paper2', 'paper1', 'paper0'])
        with self.env(), patch.dict(os.environ, {'LAB_STORAGE': str(store.root)}): asyncio.run(run())
        with self.assertRaises(StoreError): init(store)

    def test_invalid_initial_state_rejected_before_files_created(self):
        """只有內部恢復／檢查會傳初始狀態；未知欄位及關聯仍先驗證再建檔。"""
        for number, mutate in enumerate([
            lambda state: state.update(news=[]),
            lambda state: state['people'][0].update(publishState='draft'),
            lambda state: state['publications'][0]['authors'][0].update(personId='missing'),
        ]):
            state = state_from_sample(sample_data()); mutate(state)
            store = Store(self.root / f'invalid{number}')
            with self.assertRaises(ValueError): store.initialize(state)
            self.assertFalse(store.root.exists())

    def test_people_sort_photo_and_bidirectional_papers(self):
        """同姓穩定排序、直接照片、同名不誤連結及修改作者後反向關聯即時變更。"""
        def seed(state):
            state['people'] = [{**Person(id=i, familyName=f, givenName=g, portrait={'url': '/assets/photo.jpg'}).model_dump(), **new_metadata()}
                               for i, f, g in [('z', 'Wang', 'Zoe'), ('b', 'chen', 'Bob'), ('a2', 'Chen', 'Amy'), ('a1', 'CHEN', 'Amy')]]
            state['publications'] = [{**Publication(id='paper', title='Paper', year=2026,
                authors=[Author(personId='a1', name='Amy'), Author(personId='a1', name='Repeated'), Author(name='Bob chen')]).model_dump(), **new_metadata()}]
        self.store.change(seed)
        async def run(app):
            token, session = app.state.security.issue(self.user)
            auth = {'token': token, 'csrf': session.csrf}
            people = (await request(public_app(), '/api/v1/public/people'))[1]
            self.assertEqual([p['id'] for p in people], ['a1', 'a2', 'b', 'z'])
            details = [(await request(public_app(), '/api/v1/public/people/' + p['id']))[1] for p in people]
            self.assertEqual([[paper['id'] for paper in p['publications']] for p in details], [['paper'], [], [], []])
            self.assertEqual(people[0]['portrait']['url'], '/assets/photo.jpg')
            self.assertEqual(self.public.read(('assets',))['assets'], [])
            admin_people = (await request(app, '/api/v1/admin/content/people', **auth))[1]['items']
            self.assertEqual([p['data']['id'] for p in admin_people], ['a1', 'a2', 'b', 'z'])
            self.assertEqual(admin_people[0]['data']['publicationIds'], ['paper'])
            self.assertEqual((await request(app, '/api/v1/admin/content/people/a1?version=1', method='DELETE', **auth))[0], 422)
            paper = self.store.read(('publications',))['publications'][0]
            data = {k: paper[k] for k in MODELS['publications'].model_fields}
            data['authors'] = [{'name': 'Bob chen', 'personId': 'b'}]
            self.assertEqual((await request(app, '/api/v1/admin/content/publications/paper', method='PUT', body={'data': data, 'version': 1}, **auth))[0], 200)
            by_id = {p['data']['id']: p['data'] for p in (await request(app, '/api/v1/admin/content/people', **auth))[1]['items']}
            self.assertEqual(by_id['a1']['publicationIds'], [])
            self.assertEqual(by_id['b']['publicationIds'], ['paper'])
            self.assertEqual((await request(app, '/api/v1/admin/content/people/a1?version=1', method='DELETE', **auth))[0], 200)
            data['authors'][0]['personId'] = 'missing'
            self.assertEqual((await request(app, '/api/v1/admin/content/publications/paper', method='PUT', body={'data': data, 'version': 2}, **auth))[0], 422)
        self.with_app(run)



if __name__ == '__main__':
    unittest.main()
