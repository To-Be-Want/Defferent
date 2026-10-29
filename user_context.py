"""Request-local identity and durable storage boundaries (never process-global DB switches)."""
import os
import re
from contextvars import ContextVar
from pathlib import Path
import sqlite3

PROJECT_ROOT = Path(__file__).resolve().parent
current_user = ContextVar("cosight_current_user", default=None)


class ClosingConnection(sqlite3.Connection):
    """sqlite's default context manager commits but does NOT close the connection."""
    def __exit__(self, *args):
        try:
            return super().__exit__(*args)
        finally:
            self.close()


def data_root():
    return Path(os.getenv("COSIGHT_DATA_DIR", str(PROJECT_ROOT / "data"))).resolve()


def user_root():
    user = current_user.get()
    if not user or not re.fullmatch(r"[a-f0-9]{32}", user["id"]):
        raise PermissionError("需要有效的登录用户")
    root = data_root() / "users" / user["id"]
    root.mkdir(parents=True, exist_ok=True)
    return root


def contained_path(root, relative):
    root = Path(root).resolve()
    path = (root / relative).resolve()
    if not path.is_relative_to(root):
        raise PermissionError("不允许访问此路径")
    return path


def scoped_session_id(value):
    user = current_user.get()
    if not user:
        return value
    # Prefix is computed by the server, not accepted as an identity claim.
    return user["id"] + ":" + str(value or "default")[:160]
