"""Explicit, backed-up import into an EMPTY account learning space; never overwrite user data.

Run without --apply first. Stop the learning server before applying.
Source learning databases and original uploads are retained unchanged.
"""
import argparse
import hashlib
import json
import re
import shutil
import sqlite3
import sys
import uuid
from contextlib import closing
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from user_context import current_user, data_root

LEARNING_TABLES = ("subjects", "blocks", "exam_profile", "plans", "plan_events", "quizzes",
                   "questions", "qa_log", "mistakes", "mistake_summaries", "past_papers", "exam_questions")


def read_database(path):
    conn = sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def tables(conn):
    return {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def counts(path):
    if not path.is_file():
        return {}
    with closing(read_database(path)) as conn:
        return {name: conn.execute('SELECT COUNT(*) FROM "' + name + '"').fetchone()[0]
                for name in sorted(tables(conn)) if re.fullmatch(r"[a-z_]+", name) and not name.startswith("sqlite_")}


def snapshot(source, destination):
    with closing(read_database(source)) as src, closing(sqlite3.connect(destination)) as dst:
        src.backup(dst)


def readable(value):
    """Recover known UTF-8-as-Latin-1 mojibake only when decoding is valid."""
    try:
        return value.encode("latin1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return value


def inspect_import(project, storage, user_id):
    project, storage = Path(project).resolve(), Path(storage).resolve()
    if not re.fullmatch(r"[a-f0-9]{32}", user_id):
        raise ValueError("账号ID不合法")
    with closing(read_database(storage / "accounts.db")) as conn:
        row = conn.execute("SELECT id,username FROM users WHERE id=?", (user_id,)).fetchone()
        if not row:
            raise ValueError("目标账号不存在")
        user = dict(row)
    destination = storage / "users" / user_id
    if destination.is_symlink() or not destination.resolve().is_relative_to((storage / "users").resolve()):
        raise ValueError("目标用户目录不安全")
    target_counts = counts(destination / "study.db")
    if any(target_counts.get(name, 0) for name in LEARNING_TABLES):
        raise ValueError("该账号已有学习数据，拒绝覆盖。需要另外制定合并方案。")
    if counts(destination / "study_library" / "study_library.db").get("materials", 0):
        raise ValueError("该账号已有资料，拒绝覆盖。需要另外制定合并方案。")
    source_db = project / "study.db"
    if not source_db.is_file() or source_db.resolve() == (destination / "study.db").resolve():
        raise ValueError("旧学习数据库不存在或源/目标相同")
    source_counts = counts(source_db)
    by_hash = {}
    for folder in (project / "material", project / "upload_files"):
        if not folder.is_dir():
            continue
        for path in sorted(folder.rglob("*")):
            if not path.is_file() or path.is_symlink() or not path.resolve().is_relative_to(folder.resolve()):
                continue
            if path.suffix.lower() not in {".pdf", ".docx", ".pptx", ".txt", ".md", ".json", ".png", ".jpg", ".jpeg", ".gif"}:
                continue
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            name = readable(path.name)
            item = by_hash.setdefault(digest, {"sha256": digest, "filename": name, "source": path,
                                               "aliases": [], "subject": ""})
            item["aliases"].append(str(path.resolve()))
            chinese_count = lambda text: sum('\u4e00' <= char <= '\u9fff' for char in text)
            if chinese_count(name) > chinese_count(item["filename"]):
                item.update(filename=name, source=path)
    # Prefer original subject metadata when available, without importing broken old cache plans.
    library = project / "study_library" / "study_library.db"
    if library.is_file():
        with closing(read_database(library)) as conn:
            if "materials" in tables(conn):
                for row in conn.execute("SELECT * FROM materials"):
                    path = Path(row["file_path"])
                    if not path.is_absolute():
                        path = project / path
                    match = next((item for item in by_hash.values() if str(path.resolve()) in item["aliases"]), None)
                    if match:
                        match["subject"] = readable(row["subject"] or "")
    return user, destination, source_counts, list(by_hash.values()), target_counts


def import_legacy(project, storage, user_id, apply=False):
    project, storage = Path(project).resolve(), Path(storage).resolve()
    user, destination, old_counts, materials, before = inspect_import(project, storage, user_id)
    report = {"user_id": user_id, "source_counts": old_counts, "target_before": before,
              "unique_materials": len(materials), "applied": False}
    marker = destination / "legacy-import.json"
    if marker.exists():
        raise ValueError("该账号已有导入记录，拒绝重复导入")
    if not apply:
        return report
    backup = storage / "backups" / ("legacy-import-" + datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8])
    backup.mkdir(parents=True, exist_ok=False)
    for source, name in ((project / "study.db", "source-study.db"),
                         (project / "study_library" / "study_library.db", "source-library.db"),
                         (destination / "study.db", "target-study.db"),
                         (destination / "study_library" / "study_library.db", "target-library.db")):
        if source.is_file():
            snapshot(source, backup / name)
    settings = destination / "study_library" / "settings.json"
    if settings.is_file():
        shutil.copy2(settings, backup / "target-settings.json")
    # Backups are completed before the target is modified. Consent settings are NOT imported.
    token = current_user.set(user)
    conn = None
    new_files = []
    try:
        import study_db
        from cosight_server.deep_research.services.study_library import _connect, classify_subject
        study_db.init_db()
        with _connect():
            pass
        conn = study_db.get_conn()
        library_path = destination / "study_library" / "study_library.db"
        conn.execute("ATTACH DATABASE ? AS legacy", (str(backup / "source-study.db"),))
        conn.execute("ATTACH DATABASE ? AS library", (str(library_path),))
        conn.execute("BEGIN IMMEDIATE")
        # Recheck under the write lock so an unexpected live writer cannot be overwritten.
        if any(conn.execute('SELECT COUNT(*) FROM "' + name + '"').fetchone()[0] for name in LEARNING_TABLES):
            raise ValueError("目标学习库已发生变化，已中止导入")
        if conn.execute("SELECT COUNT(*) FROM library.materials").fetchone()[0]:
            raise ValueError("目标资料库已发生变化，已中止导入")
        conn.execute("PRAGMA defer_foreign_keys=ON")
        source_tables = {r[0] for r in conn.execute("SELECT name FROM legacy.sqlite_master WHERE type='table'")}
        for name in LEARNING_TABLES:
            if name not in source_tables:
                continue
            src_cols = {r[1] for r in conn.execute('PRAGMA legacy.table_info("' + name + '")')}
            cols = [r[1] for r in conn.execute('PRAGMA main.table_info("' + name + '")') if r[1] in src_cols]
            quoted = ",".join('"' + col + '"' for col in cols)
            conn.execute(f'INSERT INTO main."{name}" ({quoted}) SELECT {quoted} FROM legacy."{name}"')
        if "asked_questions" in source_tables:
            for row in conn.execute("SELECT session_id,question,created_at FROM legacy.asked_questions").fetchall():
                sid = user_id + ":" + str(row["session_id"]).removeprefix(user_id + ":")
                if not conn.execute("SELECT 1 FROM asked_questions WHERE session_id=? AND question=?", (sid, row["question"])).fetchone():
                    conn.execute("INSERT INTO asked_questions(session_id,question,created_at) VALUES (?,?,?)", (sid, row["question"], row["created_at"]))
        aliases = {}
        imported = []
        for item in materials:
            upload_id = uuid.uuid4().hex
            folder = destination / "upload_files" / upload_id
            folder.mkdir(parents=True, exist_ok=False)
            path = folder / item["filename"]
            shutil.copy2(item["source"], path)
            new_files.append(path)
            if hashlib.sha256(path.read_bytes()).hexdigest() != item["sha256"]:
                raise ValueError("资料复制校验失败")
            subject = classify_subject(item["filename"], item["subject"])
            conn.execute("INSERT INTO library.materials(upload_id,filename,subject,file_path,size,created_at) VALUES (?,?,?,?,?,?)",
                         (upload_id, item["filename"], subject, str(path), path.stat().st_size,
                          datetime.fromtimestamp(item["source"].stat().st_mtime).isoformat(timespec="seconds")))
            for alias in item["aliases"]:
                aliases[alias] = str(path)
            imported.append({"upload_id": upload_id, "filename": item["filename"], "sha256": item["sha256"], "file_path": str(path)})
        for row in conn.execute("SELECT id,file_path FROM past_papers").fetchall():
            old_path = Path(row["file_path"] or "")
            if not old_path.is_absolute():
                old_path = project / old_path
            new_path = aliases.get(str(old_path.resolve()))
            if new_path:
                conn.execute("UPDATE past_papers SET file_path=? WHERE id=?", (new_path, row["id"]))
        if conn.execute("PRAGMA foreign_key_check").fetchall():
            raise ValueError("旧数据存在无效关联，已回滚")
        conn.commit()
        report.update(applied=True, backup=str(backup), imported_materials=imported,
                      target_after={name: conn.execute('SELECT COUNT(*) FROM "' + name + '"').fetchone()[0] for name in LEARNING_TABLES})
        marker.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        (backup / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        return report
    except Exception:
        if conn:
            conn.rollback()
        # Leave any copied files for recovery; originals and pre-import DB backups are untouched.
        raise
    finally:
        if conn:
            conn.close()
        current_user.reset(token)


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    try:
        from dotenv import load_dotenv
        load_dotenv(ROOT / ".env")
    except ImportError:
        pass
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--user-id", required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    report = import_legacy(ROOT, data_root(), args.user_id, args.apply)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
