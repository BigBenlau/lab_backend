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

    @classmethod
    def load(cls, admin: bool = False):
        """從環境取得設定；管理入口強制 HTTPS，開發例外僅限 loopback。"""
        dev = os.getenv('LAB_DEV', 'false') == 'true'
        origin = os.getenv('LAB_ADMIN_ORIGIN', '')
        if admin:
            parsed = urlsplit(origin)
            if parsed.path or parsed.query or parsed.fragment or parsed.username or not parsed.hostname:
                raise ValueError('LAB_ADMIN_ORIGIN 需為精確 origin，不含路徑')
            if parsed.scheme != 'https' and not (dev and parsed.scheme == 'http' and parsed.hostname in ('localhost', '127.0.0.1')):
                raise ValueError('正式管理入口必須 HTTPS')
            if dev and parsed.hostname not in ('localhost', '127.0.0.1'):
                raise ValueError('LAB_DEV 只允許 loopback 開發入口')
        return cls(Path(os.getenv('LAB_STORAGE', 'storage')).resolve(), origin, dev)
