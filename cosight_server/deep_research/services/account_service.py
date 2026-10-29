"""Small, persistent account/session service. Passwords and session tokens are never stored raw."""
import hashlib
import hmac
import os
import re
import secrets
import sqlite3
import time
from user_context import data_root, ClosingConnection

COOKIE_NAME = "cosight_session"
SESSION_SECONDS = 7 * 24 * 3600


def connect():
    root = data_root()
    root.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(root / "accounts.db", timeout=10, factory=ClosingConnection)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS users (
          id TEXT PRIMARY KEY, username TEXT UNIQUE NOT NULL,
          password_hash TEXT NOT NULL, created_at INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS sessions (
          token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id),
          csrf_token TEXT NOT NULL, expires_at INTEGER NOT NULL);
        CREATE INDEX IF NOT EXISTS idx_sessions_expiry ON sessions(expires_at);
        CREATE TABLE IF NOT EXISTS auth_attempts (
          bucket TEXT PRIMARY KEY, count INTEGER NOT NULL, started_at INTEGER NOT NULL);
    """)
    return conn


def password_hash(password, salt=None):
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=32768, r=8, p=3,
                            maxmem=64 * 1024 * 1024, dklen=32)
    return "scrypt$32768$8$3$" + salt.hex() + "$" + digest.hex()


def verify_password(password, encoded):
    try:
        kind, n, r, p, salt, digest = encoded.split("$")
        if (kind, n, r, p) != ("scrypt", "32768", "8", "3"):
            return False
        return hmac.compare_digest(password_hash(password, bytes.fromhex(salt)), encoded)
    except (ValueError, TypeError):
        return False


def create_user(username, password):
    username = username.strip().lower()
    if not re.fullmatch(r"[a-z0-9_.@+\-]{3,100}", username):
        raise ValueError("账号须为3—100位字母、数字或邮箱格式字符")
    if not 10 <= len(password) <= 128:
        raise ValueError("密码长度须为10—128位")
    user = {"id": secrets.token_hex(16), "username": username}
    encoded = password_hash(password)
    try:
        with connect() as conn:
            conn.execute("INSERT INTO users VALUES (?,?,?,?)",
                         (user["id"], username, encoded, int(time.time())))
    except sqlite3.IntegrityError:
        raise ValueError("该账号不可用") from None
    return user


def authenticate(username, password):
    if len(password) > 128 or len(username) > 100:
        return None
    with connect() as conn:
        row = conn.execute("SELECT * FROM users WHERE username=?", (username.strip().lower(),)).fetchone()
    # Perform the same expensive KDF for unknown accounts.
    encoded = row["password_hash"] if row else "scrypt$32768$8$3$" + "00" * 16 + "$" + "00" * 32
    valid = verify_password(password, encoded)
    return {"id": row["id"], "username": row["username"]} if row and valid else None


def allow_attempt(bucket, limit=10):
    now = int(time.time())
    # Hash client identifiers before persisting rate-limit state.
    bucket = hashlib.sha256(bucket.encode()).hexdigest()
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("DELETE FROM auth_attempts WHERE started_at < ?", (now - 900,))
        conn.execute("INSERT INTO auth_attempts VALUES (?,1,?) ON CONFLICT(bucket) "
                     "DO UPDATE SET count=count+1", (bucket, now))
        count = conn.execute("SELECT count FROM auth_attempts WHERE bucket=?", (bucket,)).fetchone()[0]
    return count <= limit


def create_session(user_id):
    token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    with connect() as conn:
        conn.execute("DELETE FROM sessions WHERE expires_at<=?", (int(time.time()),))
        conn.execute("INSERT INTO sessions VALUES (?,?,?,?)", (
            hashlib.sha256(token.encode()).hexdigest(), user_id, csrf, int(time.time()) + SESSION_SECONDS))
    return token, csrf


def read_session(token):
    if not token or len(token) > 128:
        return None
    with connect() as conn:
        row = conn.execute("SELECT u.id,u.username,s.csrf_token FROM sessions s JOIN users u "
                           "ON u.id=s.user_id WHERE token_hash=? AND expires_at>?",
                           (hashlib.sha256(token.encode()).hexdigest(), int(time.time()))).fetchone()
    return dict(row) if row else None


def revoke_session(token):
    with connect() as conn:
        conn.execute("DELETE FROM sessions WHERE token_hash=?", (hashlib.sha256(token.encode()).hexdigest(),))
