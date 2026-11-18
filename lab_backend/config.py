"""明確載入設定，不在匯入時建立資料或讀機密。"""
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit
import os


@dataclass(frozen=True)
class Config:
    storage: Path
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

    def accepts_origin(self, origin: str, host: str):
        """取消固定主機白名單，仍比對同源；協定取部署模式，不信任轉發標頭。"""
        scheme = 'https' if self.secure_cookie else 'http'
        if not host or any(c.isspace() or c in '/\\@?#' for c in host):
            return False
        try:
            parsed = urlsplit(f'{scheme}://{host}')
            # 讀取port也驗證數字及範圍，拒絕格式不合法的authority。
            port = parsed.port
        except ValueError:
            return False
        if not parsed.hostname or (port is not None and port == 0):
            return False
        if self.dev and parsed.hostname not in ('localhost', '127.0.0.1'):
            return False
        return origin == f'{scheme}://{host}'

    @classmethod
    def load(cls):
        """預設HTTPS；HTTP測試與本機開發必須明確選擇，不再讀固定Host／Origin。"""
        dev = os.getenv('LAB_DEV', 'false') == 'true'
        test_mode = os.getenv('LAB_HTTP_TEST', 'false')
        if test_mode not in ('true', 'false'):
            raise ValueError('LAB_HTTP_TEST 只接受 true 或 false')
        http_test = test_mode == 'true'
        if dev and http_test:
            raise ValueError('HTTP測試不可同時開啟LAB_DEV')
        return cls(Path(os.getenv('LAB_STORAGE', 'storage')).resolve(), dev, http_test)
