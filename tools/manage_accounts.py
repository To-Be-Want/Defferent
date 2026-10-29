"""Offline administration: python tools/manage_accounts.py create USER or reset-password USER."""
import argparse
import getpass
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
except ImportError:
    pass
from cosight_server.deep_research.services import account_service as accounts


def main():
    parser = argparse.ArgumentParser(description="本机账号管理（不会把密码打印或写进命令行）")
    parser.add_argument("action", choices=("create", "reset-password"))
    parser.add_argument("username")
    args = parser.parse_args()
    password = getpass.getpass("新密码（至少10位）: ")
    if password != getpass.getpass("再次输入: "):
        raise SystemExit("两次密码不一致")
    if not 10 <= len(password) <= 128:
        raise SystemExit("密码须为10—128位")
    if args.action == "create":
        user = accounts.create_user(args.username, password)
    else:
        with accounts.connect() as conn:
            row = conn.execute("SELECT id,username FROM users WHERE username=?", (args.username.strip().lower(),)).fetchone()
            if not row:
                raise SystemExit("账号不存在")
            conn.execute("UPDATE users SET password_hash=? WHERE id=?", (accounts.password_hash(password), row["id"]))
            conn.execute("DELETE FROM sessions WHERE user_id=?", (row["id"],))
            user = dict(row)
    print("账号操作完成：", user["username"])


if __name__ == "__main__":
    main()
