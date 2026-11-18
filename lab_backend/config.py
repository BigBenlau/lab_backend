"""明確載入設定，不在匯入時建立資料或讀機密。"""
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit
import os


@dataclass(frozen=True)
class Config:
    storage: Path
    admin_origin: str
    dev: bool
    http_test: bool = False

    @property
    def secure_cookie(self):
        """只有明確選用的 HTTP 測試／本機開發可停用 Secure。"""
        return not (self.dev or self.http_test)

    @property
    def session_cookie(self):
        """HTTP 不能使用 __Host- 前綴，並與正式登入使用不同名稱。"""
        if self.http_test:
            return 'lab_session_http_test'
        return 'lab_session_dev' if self.dev else '__Host-lab_session'

    @classmethod
    def load(cls, admin: bool = False):
        """預設強制 HTTPS；遠端 HTTP 測試須單獨開啟，不擴張 LAB_DEV。"""
        dev = os.getenv('LAB_DEV', 'false') == 'true'
        test_mode = os.getenv('LAB_HTTP_TEST', 'false')
        if test_mode not in ('true', 'false'):
            raise ValueError('LAB_HTTP_TEST 只接受 true 或 false')
        http_test = test_mode == 'true'
        origin = os.getenv('LAB_ADMIN_ORIGIN', '')
        if admin:
            parsed = urlsplit(origin)
            if parsed.path or parsed.query or parsed.fragment or parsed.username or not parsed.hostname:
                raise ValueError('LAB_ADMIN_ORIGIN 需為精確 origin，不含路徑')
            if http_test and (dev or parsed.scheme != 'http'):
                raise ValueError('HTTP 測試需 http Origin，且不可同時開啟 LAB_DEV')
            if parsed.scheme != 'https' and not (parsed.scheme == 'http' and (http_test or dev)):
                raise ValueError('正式管理入口必須 HTTPS')
            if dev and parsed.hostname not in ('localhost', '127.0.0.1'):
                raise ValueError('LAB_DEV 只允許 loopback 開發入口')
        return cls(Path(os.getenv('LAB_STORAGE', 'storage')).resolve(), origin, dev, http_test)
