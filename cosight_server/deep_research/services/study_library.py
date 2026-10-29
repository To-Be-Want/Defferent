import json
import re
import sqlite3
import threading
from datetime import date, datetime
from pathlib import Path


_LOCK = threading.Lock()


def _root():
    from user_context import current_user, user_root
    path = (user_root() if current_user.get() else Path.cwd()) / "study_library"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _connect():
    from user_context import ClosingConnection
    connection = sqlite3.connect(str(_root() / "study_library.db"), timeout=20, factory=ClosingConnection)
    connection.row_factory = sqlite3.Row
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS materials (
            upload_id TEXT PRIMARY KEY,
            filename TEXT NOT NULL,
            subject TEXT NOT NULL,
            file_path TEXT NOT NULL,
            size INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_materials_subject ON materials(subject);
        CREATE TABLE IF NOT EXISTS study_plans (
            plan_id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            plan_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        """
    )
    columns = {row[1] for row in connection.execute('PRAGMA table_info(materials)')}
    if 'subject_source' not in columns:
        connection.execute("ALTER TABLE materials ADD COLUMN subject_source TEXT NOT NULL DEFAULT 'auto'")
        connection.commit()
    if 'kind' not in columns:
        connection.execute("ALTER TABLE materials ADD COLUMN kind TEXT NOT NULL DEFAULT ''")
        connection.commit()
    return connection


def classify_subject(filename, requested=""):
    requested = (requested or "").strip()
    if requested and requested != "自动分类":
        return requested[:40]
    text = filename.lower()
    rules = (
        ("离散数学", ("离散", "discrete", "图论", "数理逻辑", "集合论")),
        ("高等数学", ("高数", "高等数学", "calculus", "微积分", "极限", "导数", "积分")),
        ("线性代数", ("线代", "线性代数", "linear algebra", "矩阵", "行列式")),
        ("概率论", ("概率", "统计", "probability", "statistics")),
        ("大学物理", ("大学物理", "物理", "physics", "电磁学", "牛顿定律")),
        ("大学英语", ("英语", "english", "cet4", "cet6", "四级", "六级")),
        ("程序设计", ("c++", "java", "python", "程序设计", "数据结构", "算法")),
    )
    for subject, keywords in rules:
        if any(keyword in text for keyword in keywords):
            return subject
    return "待分类"


def save_material(upload_id, filename, file_path, size, requested_subject=""):
    subject = classify_subject(filename, requested_subject)
    with _LOCK, _connect() as connection:
        connection.execute(
            """INSERT OR REPLACE INTO materials
               (upload_id, filename, subject, file_path, size, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (upload_id, filename, subject, str(file_path), int(size), datetime.now().isoformat(timespec="seconds")),
        )
        connection.commit()
    if requested_subject and requested_subject != '自动分类':
        update_material_subject(upload_id, subject)
    elif subject == '待分类':
        try:
            from material_analyst import extract_pdf_text
            text = extract_pdf_text(str(file_path), max_chars=5000)
            learn_subject_from_text(file_path, text)
            classified = classify_subject(text)
            if classified != '待分类': subject = classified
        except Exception:
            pass  # Upload remains usable even when OCR is unavailable.
    return subject


def list_materials(subject=""):
    with _LOCK, _connect() as connection:
        if subject:
            rows = connection.execute(
                "SELECT * FROM materials WHERE subject=? ORDER BY created_at DESC", (subject,)
            ).fetchall()
        else:
            rows = connection.execute("SELECT * FROM materials ORDER BY subject, created_at DESC").fetchall()
    return [{key: row[key] for key in row.keys() if key != "file_path"} for row in rows]


def index_existing_uploads(upload_root):
    """把升级前已落盘、但还没有资料索引的上传文件自动补录。"""
    root = Path(upload_root)
    if not root.is_dir():
        return 0
    indexed = 0
    with _LOCK, _connect() as connection:
        known = {row[0] for row in connection.execute("SELECT upload_id FROM materials").fetchall()}
    for folder in root.iterdir():
        if not folder.is_dir() or folder.name in known:
            continue
        files = [item for item in folder.iterdir() if item.is_file()]
        if not files:
            continue
        source = files[0]
        save_material(folder.name, source.name, source, source.stat().st_size)
        indexed += 1
    return indexed


def update_material_subject(upload_id, subject):
    subject = (subject or "待分类").strip()[:40]
    with _LOCK, _connect() as connection:
        cursor = connection.execute("UPDATE materials SET subject=?,subject_source='manual' WHERE upload_id=?", (subject, upload_id))
        connection.commit()
    return cursor.rowcount > 0


def learn_subject_from_text(file_path, text):
    subject = classify_subject(text[:10000])
    if subject == '待分类': return False
    with _LOCK, _connect() as connection:
        cursor = connection.execute("UPDATE materials SET subject=? WHERE file_path=? AND subject='待分类' AND subject_source='auto'", (subject,str(file_path)))
        return cursor.rowcount > 0


def material_path(upload_id):
    with _LOCK, _connect() as connection:
        row = connection.execute("SELECT file_path FROM materials WHERE upload_id=?", (upload_id,)).fetchone()
    return Path(row["file_path"]) if row else None


def save_study_plan(plan_id, plan_data):
    if not isinstance(plan_data, dict):
        return
    # 这是旧版“完整计划缓存”，只能接收明确标记的学习计划。此前仅靠
    # “复习/考试”等关键词判断，会把普通研究任务及其工具调用日志误收进来。
    is_explicit_study_plan = (
        plan_data.get("content_type") == "study_plan"
        or plan_data.get("plan_type") == "study"
        or isinstance(plan_data.get("daily_tasks"), list)
    )
    if not is_explicit_study_plan:
        return
    title = str(plan_data.get("title") or "未命名复习计划")
    now = datetime.now().isoformat(timespec="seconds")
    payload = json.dumps(plan_data, ensure_ascii=False)
    with _LOCK, _connect() as connection:
        connection.execute(
            """INSERT INTO study_plans(plan_id, title, plan_json, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(plan_id) DO UPDATE SET title=excluded.title,
               plan_json=excluded.plan_json, updated_at=excluded.updated_at""",
            (plan_id, title, payload, now, now),
        )
        connection.commit()


def _today_lines(plan, target):
    text = json.dumps(plan, ensure_ascii=False, indent=2)
    markers = (
        target.isoformat(), f"{target.month}月{target.day}日", f"{target.month}/{target.day}",
        f"{target.month:02d}/{target.day:02d}", "今日", "今天",
    )
    # 旧代码把 "\\t" 写进了 strip 字符集合，实际会把字段首尾的字母 t
    # 也删掉（tool_args -> ool_args、timestamp -> imestamp）。
    lines = [line.strip().strip(",").strip('"') for line in text.splitlines()]
    hits = []
    for index, line in enumerate(lines):
        if any(marker in line for marker in markers):
            for candidate in lines[max(0, index - 1):min(len(lines), index + 5)]:
                if candidate and candidate not in hits and len(candidate) <= 300:
                    hits.append(candidate)
    return hits[:20]


def get_today_plan(target_date=None):
    target = date.fromisoformat(target_date) if target_date else date.today()
    with _LOCK, _connect() as connection:
        rows = connection.execute("SELECT * FROM study_plans ORDER BY updated_at DESC").fetchall()
    for row in rows:
        plan = json.loads(row["plan_json"])
        lines = _today_lines(plan, target)
        if not lines:
            steps = plan.get("steps") or []
            lines = [str(step) for step in steps[:8]]
        if lines:
            return {
                "found": True, "date": target.isoformat(), "plan_id": row["plan_id"],
                "title": row["title"], "tasks": lines, "updated_at": row["updated_at"],
            }
    return {"found": False, "date": target.isoformat(), "title": "今日复习计划", "tasks": []}
