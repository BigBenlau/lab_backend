"""Demo 帳密登入與帳戶管理；public 匿名唯讀，所有登入帳戶都是 admin。"""
from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import secrets
import threading
import time
import uuid
from typing import Literal

from argon2 import PasswordHasher
from argon2.exceptions import VerificationError, InvalidHashError
from fastapi import APIRouter, HTTPException, Query, Request, Response
from pydantic import Field

from .models import Audit, Id, Items, LoginName, Model, Name, NewPassword, Ok, Password, Role, User, now

PASSWORDS = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=1)
SESSION_SECONDS = 8 * 60 * 60


def deny(detail='沒有操作權限', status=403):
    """統一中止 API 操作；錯誤訊息不得附上密碼或 Cookie。"""
    raise HTTPException(status, detail)


def audit(state, actor, action, target='', result='success'):
    """只記動作及 ID，與內容在同一交易提交，不記錄請求內容。"""
    state['audit'].append({'id': uuid.uuid4().hex, 'at': now(), 'actor': actor,
                           'action': action, 'target': target, 'result': result})


def verify_password(encoded, value):
    """用 Argon2 比對密碼；損壞的雜湊同樣視為驗證失敗。"""
    try:
        return PASSWORDS.verify(encoded, value)
    except (VerificationError, InvalidHashError):
        return False


def strong_password(value):
    """CLI 與 API 共用新密碼規則；登入舊密碼不套用這個長度下限。"""
    from pydantic import TypeAdapter
    return TypeAdapter(NewPassword).validate_python(value)


@dataclass
class Session:
    user_id: str
    auth_version: int
    csrf: str
    expires: float


class Security:
    """單一程序的基本 Cookie Session；重啟即登出，不快取業務內容。"""
    def __init__(self, config):
        """保存設定與記憶體 Session，不再讀取 MFA 金鑰。"""
        self.config = config
        self.cookie = 'lab_session_dev' if config.dev else '__Host-lab_session'
        self.sessions: dict[str, Session] = {}
        self.lock = threading.RLock()
        self.password_slots = threading.BoundedSemaphore(2)
        self.dummy_hash = PASSWORDS.hash(secrets.token_urlsafe(32))

    @contextmanager
    def password_work(self):
        """避免同時執行過多 Argon2；昂貴運算不持有 SQLite 寫鎖。"""
        if not self.password_slots.acquire(blocking=False):
            deny('密碼驗證忙碌，請稍後重試', 429)
        try:
            yield
        finally:
            self.password_slots.release()

    @staticmethod
    def digest(token):
        """Session 索引只保存 Cookie 的雜湊。"""
        return hashlib.sha256(token.encode()).hexdigest()

    def _prune(self):
        """在 Session 存取時移除已超過八小時的項目。"""
        stamp = time.monotonic()
        self.sessions = {k: s for k, s in self.sessions.items() if s.expires > stamp}

    def issue(self, user):
        """只為啟用的 admin 建立 Session，不建立公開讀者帳戶。"""
        if user['role'] != 'admin':
            deny()
        if not user['active']:
            deny('請重新登入', 401)
        token = secrets.token_urlsafe(48)
        session = Session(user['id'], user['authVersion'], secrets.token_urlsafe(32), time.monotonic() + SESSION_SECONDS)
        with self.lock:
            self._prune()
            if len(self.sessions) >= 1000:
                deny('Session 容量已滿', 503)
            self.sessions[self.digest(token)] = session
        return token, session

    def actor(self, request, state):
        """每次讀最新帳戶；停用或改密碼後，舊 Session 不可繼續寫入。"""
        with self.lock:
            self._prune()
            session = self.sessions.get(self.digest(request.cookies.get(self.cookie, '')))
            user = next((u for u in state['users'] if session and u['id'] == session.user_id), None)
            if not user or not user['active'] or user['authVersion'] != session.auth_version:
                deny('請重新登入', 401)
            if user['role'] != 'admin':
                deny()
            if request.method not in ('GET', 'HEAD', 'OPTIONS'):
                if not secrets.compare_digest(request.headers.get('x-csrf-token', ''), session.csrf):
                    deny('CSRF 驗證失敗')
            return user, session

    def preflight(self, request, store):
        """匿名請求在解析 body 及讀資料庫前拒絕；寫交易仍要重查 actor。"""
        with self.lock:
            self._prune()
            if self.digest(request.cookies.get(self.cookie, '')) not in self.sessions:
                deny('請重新登入', 401)
        return self.actor(request, store.read(('users',)))

    def revoke(self, user_id):
        """帳戶變更時撤銷該帳戶的所有登入。"""
        with self.lock:
            self.sessions = {k: s for k, s in self.sessions.items() if s.user_id != user_id}

    def logout(self, request):
        """只移除這次瀏覽器登入的 Session。"""
        with self.lock:
            self.sessions.pop(self.digest(request.cookies.get(self.cookie, '')), None)


class Credentials(Model):
    login: LoginName
    password: Password


class NewLocalUser(Credentials):
    displayName: Name
    password: NewPassword


class ChangePassword(Model):
    currentPassword: Password
    newPassword: NewPassword


class Access(Model):
    active: bool
    version: int = Field(ge=1)


class UserInfo(Model):
    """回應白名單獨立於儲存模型，新增私有欄位不會自動洩漏給瀏覽器。"""
    id: Id
    displayName: Name
    login: LoginName
    role: Role
    active: bool
    version: int = Field(ge=1)
    createdAt: str


class Identity(Model):
    user: UserInfo
    csrf: str


class PasswordChanged(Ok):
    signInRequired: Literal[True]


class AuditPage(Items[Audit]):
    total: int = Field(ge=0)
    offset: int = Field(ge=0)
    warnings: list[str]


def public_user(user):
    """管理畫面可讀帳戶資料，不包含密碼雜湊或授權版本。"""
    return {key: user[key] for key in UserInfo.model_fields}


def same_credentials(current, checked):
    """驗密碼後確認帳戶沒有被停用或並行修改憑證。"""
    return bool(current and checked and current['active'] and current['role'] == checked['role'] == 'admin'
                and current['authVersion'] == checked['authVersion'] and current['passwordHash'] == checked['passwordHash'])


def build_user(body: NewLocalUser):
    """網頁及 CLI 共用帳戶組裝；呼叫時不得持有 SQLite 寫交易。"""
    return User(id=uuid.uuid4().hex, displayName=body.displayName, login=body.login,
                passwordHash=PASSWORDS.hash(body.password)).model_dump()


router = APIRouter(prefix='/api/v1/auth', tags=['auth'])
admin_router = APIRouter(prefix='/api/v1/admin', tags=['users'])


@router.post('/login', response_model=Identity)
def login(body: Credentials, request: Request, response: Response):
    """只驗帳號密碼；資料庫再次確認憑證後才建立 Cookie Session。"""
    store, sec = request.app.state.store, request.app.state.security
    checked = next((u for u in store.read(('users',))['users'] if u['login'] == body.login), None)
    with sec.password_work():
        valid = verify_password(checked['passwordHash'] if checked else sec.dummy_hash, body.password)
    if not valid or not checked:
        deny('帳號或密碼錯誤', 401)
    def operation(state):
        """登入審計與最新帳戶檢查在同一交易中完成。"""
        user = next((u for u in state['users'] if u['id'] == checked['id']), None)
        if not same_credentials(user, checked):
            deny('帳號或密碼錯誤', 401)
        audit(state, user['id'], 'auth.login')
        return user
    user = store.change(operation, resources=('users',))
    token, session = sec.issue(user)
    response.set_cookie(sec.cookie, token, secure=not sec.config.dev, httponly=True,
                        samesite='strict', path='/', max_age=SESSION_SECONDS)
    return {'user': public_user(user), 'csrf': session.csrf}


@router.get('/session', response_model=Identity)
def session(request: Request):
    """重新整理管理頁時恢復登入狀態。"""
    user, session = request.app.state.security.actor(request, request.app.state.store.read(('users',)))
    return {'user': public_user(user), 'csrf': session.csrf}


@router.post('/logout', response_model=Ok)
def logout(request: Request, response: Response):
    """撤銷目前登入並移除 Cookie。"""
    sec = request.app.state.security
    sec.logout(request)
    response.delete_cookie(sec.cookie, path='/', secure=not sec.config.dev, httponly=True, samesite='strict')
    return {'ok': True}


@router.post('/password', response_model=PasswordChanged)
def password(body: ChangePassword, request: Request):
    """驗證目前密碼後更新，無需 TOTP 或額外近期驗證步驟。"""
    store, sec = request.app.state.store, request.app.state.security
    checked, _ = sec.actor(request, store.read(('users',)))
    with sec.password_work():
        if not verify_password(checked['passwordHash'], body.currentPassword):
            deny('目前密碼錯誤', 400)
        encoded = PASSWORDS.hash(body.newPassword)
    def operation(state):
        """更新密碼與授權版本，讓其他已開啟頁面的舊 Session 失效。"""
        user, _ = sec.actor(request, state)
        if not same_credentials(user, checked):
            deny('帳戶已變更，請重新登入', 409)
        user.update(passwordHash=encoded, authVersion=user['authVersion'] + 1, version=user['version'] + 1)
        audit(state, user['id'], 'auth.password')
        return user['id']
    sec.revoke(store.change(operation, resources=('users',)))
    return {'ok': True, 'signInRequired': True}


@admin_router.get('/users', response_model=Items[UserInfo])
def list_users(request: Request):
    """取得帳戶列表；身份檢查與資料讀取使用同一份快照。"""
    state = request.app.state.store.read(('users',))
    request.app.state.security.actor(request, state)
    return {'items': [public_user(user) for user in state['users']]}


@admin_router.post('/users', status_code=201, response_model=UserInfo)
def create_user(body: NewLocalUser, request: Request):
    """登入管理員直接新增帳密帳戶；不再有待註冊狀態或金鑰設定。"""
    store, sec = request.app.state.store, request.app.state.security
    sec.actor(request, store.read(('users',)))
    with sec.password_work():
        user = build_user(body)
    def operation(state):
        """鎖內重查權限及唯一名稱，帳戶與審計一起提交。"""
        actor, _ = sec.actor(request, state)
        if any(u['login'] == user['login'] for u in state['users']):
            deny('登入名稱已存在', 409)
        state['users'].append(user)
        audit(state, actor['id'], 'user.created', user['id'])
        return public_user(user)
    return store.change(operation, resources=('users',))


@admin_router.patch('/users/{user_id}', response_model=UserInfo)
def update_user(user_id: Id, body: Access, request: Request):
    """只改啟用狀態，不接受自訂 role；最後一名管理員不可停用。"""
    def operation(state):
        """以版本避免覆寫其他管理員的操作。"""
        actor, _ = request.app.state.security.actor(request, state)
        user = next((u for u in state['users'] if u['id'] == user_id), None)
        if user is None:
            deny('找不到帳戶', 404)
        if body.version != user['version']:
            deny('版本衝突', 409)
        user.update(active=body.active, version=user['version'] + 1, authVersion=user['authVersion'] + 1)
        audit(state, actor['id'], 'user.access_changed', user_id)
        return public_user(user)
    result = request.app.state.store.change(operation, resources=('users',))
    request.app.state.security.revoke(user_id)
    return result


@admin_router.get('/audit', response_model=AuditPage)
def list_audit(request: Request, offset: int = Query(default=0, ge=0), limit: int = Query(default=100, ge=1, le=200)):
    """分頁查看核心內容操作紀錄。"""
    request.app.state.security.actor(request, request.app.state.store.read(('users',)))
    return request.app.state.store.read_audit(offset, limit)
