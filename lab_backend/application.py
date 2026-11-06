"""兩個入口共用設定/錯誤處理；公開 app 不匯入 auth。"""
from contextlib import asynccontextmanager
from pathlib import Path
import logging
import uuid

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException as StarletteHTTPException
from urllib.parse import urlsplit

from .config import Config
from .models import ApiError
from .store import Store, StoreError, MAX_REQUEST, json_loads


class Boundary:
    """先限制串流 body，再交 FastAPI 解析，防止無 Content-Length 繞過。"""
    def __init__(self, app, config, admin=False):
        """保存入口設定；admin 決定是否套用身份與來源檢查。"""
        self.app, self.config, self.admin = app, config, admin

    async def __call__(self, scope, receive, send):
        """在路由解析前限制請求，再為所有應用回應附加安全標頭。"""
        if scope['type'] != 'http':
            return await self.app(scope, receive, send)
        headers = dict(scope['headers'])
        request_id = uuid.uuid4().hex
        async def secured(message):
            """覆蓋可能衝突的標頭，讓錯誤回應也禁止快取與嵌入。"""
            if message['type'] == 'http.response.start':
                additions = [(b'cache-control', b'no-store'), (b'x-content-type-options', b'nosniff'),
                             (b'referrer-policy', b'no-referrer'), (b'x-frame-options', b'DENY'),
                             (b'x-request-id', request_id.encode()),
                             (b'content-security-policy', b"default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")]
                message['headers'] = [(k, v) for k, v in message.get('headers', []) if k.lower() not in {a[0] for a in additions}] + additions
            await send(message)
        async def reject(status, detail):
            """前置拒絕沿用安全標頭，不進入業務路由。"""
            await JSONResponse({'detail': detail}, status_code=status)(scope, receive, secured)
        if self.admin and scope['method'] not in ('GET', 'HEAD', 'OPTIONS'):
            if headers.get(b'origin', b'').decode('latin1') != self.config.admin_origin:
                return await reject(403, 'Origin 驗證失敗')
        if self.admin and scope['path'].startswith('/api/'):
            request = Request(scope)
            security = request.app.state.security
            try:
                if (scope['path'], scope['method']) != ('/api/v1/auth/login', 'POST'):
                    # 在讀 body / FastAPI 欄位驗證前拒絕無 Session 請求。
                    await run_in_threadpool(security.preflight, request, request.app.state.store)
            except HTTPException as exc:
                return await reject(exc.status_code, exc.detail)
        body = b''
        while True:
            message = await receive()
            if message['type'] == 'http.disconnect':
                return
            body += message.get('body', b'')
            if len(body) > MAX_REQUEST:
                return await reject(413, '請求內容過大')
            if not message.get('more_body', False):
                break
        if self.admin and body:
            if headers.get(b'content-type', b'').split(b';')[0].strip() != b'application/json':
                return await reject(415, '僅接受 application/json')
            try:
                json_loads(body.decode('utf-8'))
            except (ValueError, UnicodeError, RecursionError):
                return await reject(400, 'JSON 格式無效或含重複欄位')
        sent = False
        async def replay():
            """將已檢查的 body 交還 ASGI 下游，避免重讀網路串流。"""
            nonlocal sent
            if not sent:
                sent = True
                return {'type': 'http.request', 'body': body, 'more_body': False}
            return await receive()
        await self.app(scope, replay, secured)


def create_app(admin=False):
    """建立指定入口的路由；此步驟不開 socket，也不初始化資料。"""
    config = Config.load(admin=admin)
    store = Store(config.storage, public=not admin)

    @asynccontextmanager
    async def lifespan(app):
        """服務期間持有管理鎖；先確認 SQLite 可讀，再接受帳密登入。"""
        if admin:
            from .auth import Security
            with store.maintenance():
                store.recover_journals()
                app.state.security = Security(config)
                store.read(('users',))
                yield
        else:
            store.read()
            yield

    errors = {404: '資料或路徑不存在', 405: '不支援此方法', 413: '請求超過 256 KiB',
              422: '欄位型別、範圍或關聯無效', 503: '儲存或服務暫不可用'}
    if admin:
        errors.update({400: '操作不允許或 JSON 格式錯誤', 401: '登入失效或帳密錯誤',
                       403: 'Origin、CSRF 或權限驗證失敗', 409: '版本、ID 或帳號衝突',
                       415: '非 application/json', 429: '密碼運算忙碌'})
    app = FastAPI(title='Lab Admin API' if admin else 'Lab Public API', version='7.0.0',
                  description='修訂版管理契約：14 個操作模板；Cookie 帳密登入，正式安全驗收另行處理。' if admin else '修訂版公開契約：4 個 GET；匿名唯讀 SQLite，儲存即公開，僅供 Docker 內網。',
                  servers=[{'url': '/', 'description': '管理頁同源 gateway'}] if admin else [{'url': 'http://lab-public-api:8000', 'description': 'Docker lab-content 內網；瀏覽器不可直連'}],
                  responses={code: {'model': ApiError, 'description': detail} for code, detail in errors.items()},
                  docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    app.state.store, app.state.config = store, config

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException):
        """統一 HTTP 錯誤格式，不把請求內容寫入錯誤回應。"""
        return JSONResponse({'detail': exc.detail}, status_code=exc.status_code, headers=exc.headers)

    @app.exception_handler(RequestValidationError)
    @app.exception_handler(ValidationError)
    async def validation_error(request: Request, exc):
        # Pydantic errors() 可能包含原始 password/token，不可直接回傳或記錄。
        """只回傳錯誤欄位位置，避免 Pydantic 原始輸入洩漏秘密。"""
        return JSONResponse({'detail': '欄位驗證失敗', 'fields': [list(e['loc']) for e in exc.errors()]}, status_code=422)

    @app.exception_handler(ValueError)
    async def value_error(request: Request, exc):
        """回報業務規則錯誤；呼叫端的訊息不得包含敏感輸入。"""
        return JSONResponse({'detail': str(exc)}, status_code=422)

    @app.exception_handler(StoreError)
    async def unavailable(request: Request, exc):
        """隱藏檔案路徑與儲存例外細節，提醒重新讀取確認結果。"""
        return JSONResponse({'detail': '儲存不可用，請聯絡管理員並重新讀取確認操作結果'}, status_code=503)

    @app.exception_handler(Exception)
    async def unexpected(request: Request, exc):
        """未知例外只記類型，不將 traceback 或原始資料回傳。"""
        logging.getLogger('lab').error('request_failed type=%s', type(exc).__name__)
        return JSONResponse({'detail': '服務暫時不可用'}, status_code=503)

    @app.get('/healthz', include_in_schema=False)
    def health():
        """只表示程序仍可回應；內容故障不能被誤當成程序退出。"""
        return {'ok': True}

    @app.get('/readyz', include_in_schema=False)
    def ready():
        """驗證所有可服務內容；只供容器／監測使用，不由gateway代理。"""
        store.read()
        return {'ok': True}

    if admin:
        from . import auth, content
        for router in (auth.router, auth.admin_router, content.router):
            app.include_router(router)
        host = urlsplit(config.admin_origin).hostname
        app.add_middleware(TrustedHostMiddleware, allowed_hosts=[host, '127.0.0.1', 'localhost'])
        if config.dev:
            app.mount('/', StaticFiles(directory=Path(__file__).resolve().parent.parent / 'admin_frontend/admin', html=True))
    else:
        from .public import router
        app.include_router(router)
    app.add_middleware(Boundary, config=config, admin=admin)
    if admin:
        # 驗證已由 Boundary 統一執行；在同一處描述 OpenAPI 的 Cookie／Header，
        # 避免只寫在文件，或為每個 handler 重複建立一套驗證 dependency。
        spec = app.openapi()
        spec['components']['securitySchemes'] = {'CookieSession': {
            'type': 'apiKey', 'in': 'cookie',
            'name': 'lab_session_dev' if config.dev else '__Host-lab_session'}}
        for path, operations in spec['paths'].items():
            for method, operation in operations.items():
                login = path == '/api/v1/auth/login' and method == 'post'
                operation['security'] = [] if login else [{'CookieSession': []}]
                if method not in ('get', 'head', 'options'):
                    names = ['Origin'] if login else ['Origin', 'X-CSRF-Token']
                    operation.setdefault('parameters', []).extend({
                        'name': name, 'in': 'header', 'required': True,
                        'schema': {'type': 'string'},
                        'description': '須與 LAB_ADMIN_ORIGIN 完全相符' if name == 'Origin' else '取自登入或 session 回應的 csrf',
                    } for name in names)
    return app


def public_app():
    """Uvicorn 公開內容入口；僅掛載唯讀查詢路由。"""
    return create_app(admin=False)


def admin_app():
    """Uvicorn 管理入口；只由 HTTPS gateway 提供同源存取。"""
    return create_app(admin=True)
