"""One account-scoped view of current study plans and independent personal tasks."""
from datetime import date, datetime
import study_db


def connection():
    conn = study_db.get_conn()
    conn.execute("""CREATE TABLE IF NOT EXISTS personal_tasks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT NOT NULL, date TEXT NOT NULL, est_minutes INTEGER NOT NULL DEFAULT 30,
        priority TEXT NOT NULL DEFAULT 'normal', status TEXT NOT NULL DEFAULT '待办',
        notes TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL
    )""")
    conn.execute("CREATE INDEX IF NOT EXISTS personal_tasks_date ON personal_tasks(date)")
    conn.commit()
    return conn


def valid_date(value):
    if not isinstance(value, str):
        raise ValueError("日期应为YYYY-MM-DD")
    try:
        if date.fromisoformat(value).isoformat() != value:
            raise ValueError()
    except ValueError:
        raise ValueError("日期应为YYYY-MM-DD") from None
    return value


def validate_task(body):
    if not isinstance(body, dict) or set(body) - {"title", "date", "est_minutes", "priority", "status", "notes"}:
        raise ValueError("事项字段不正确")
    title = body.get("title", "")
    notes = body.get("notes", "")
    minutes = body.get("est_minutes", 30)
    if not isinstance(title, str) or not 1 <= len(title.strip()) <= 120:
        raise ValueError("事项名称须为1—120字")
    if not isinstance(notes, str) or len(notes) > 2000:
        raise ValueError("备注不能超过2000字")
    if type(minutes) is not int or not 0 <= minutes <= 1440:
        raise ValueError("预计时长须为0—1440分钟的整数")
    if body.get("priority", "normal") not in ("high", "normal", "low"):
        raise ValueError("重要程度不正确")
    if body.get("status", "待办") not in ("待办", "完成"):
        raise ValueError("任务状态不正确")
    return {"title": title.strip(), "date": valid_date(body.get("date")), "est_minutes": minutes,
            "priority": body.get("priority", "normal"), "status": body.get("status", "待办"), "notes": notes.strip()}


def create_task(body):
    item = validate_task(body)
    with connection() as conn:
        cursor = conn.execute("""INSERT INTO personal_tasks(title,date,est_minutes,priority,status,notes,created_at)
            VALUES (:title,:date,:est_minutes,:priority,:status,:notes,:created_at)""",
            {**item, "created_at": datetime.now().isoformat(timespec="seconds")})
        return {"id": cursor.lastrowid, **item}


def update_task(task_id, body):
    if not isinstance(body, dict) or not body or set(body) - {"title", "date", "est_minutes", "priority", "status", "notes"}:
        raise ValueError("事项字段不正确")
    with connection() as conn:
        old = conn.execute("SELECT * FROM personal_tasks WHERE id=?", (task_id,)).fetchone()
        if not old:
            return None
        item = validate_task({**{key: old[key] for key in ("title", "date", "est_minutes", "priority", "status", "notes")}, **body})
        conn.execute("""UPDATE personal_tasks SET title=:title,date=:date,est_minutes=:est_minutes,
            priority=:priority,status=:status,notes=:notes WHERE id=:id""", {"id": task_id, **item})
        return {"id": task_id, **item}


def delete_task(task_id):
    with connection() as conn:
        return conn.execute("DELETE FROM personal_tasks WHERE id=?", (task_id,)).rowcount > 0


def all_tasks(include_archived=False, current_day_only=False):
    with connection() as conn:
        rows = conn.execute("""WITH latest AS (
            SELECT subject_id,MAX(version) version FROM plans WHERE subject_id IS NOT NULL GROUP BY subject_id)
            SELECT p.*,s.name subject_name,s.exam_status,s.exam_date,COALESCE(b.name,'综合训练') block_name
            FROM plans p JOIN latest l ON p.subject_id=l.subject_id AND p.version=l.version
            JOIN subjects s ON s.id=p.subject_id LEFT JOIN blocks b ON b.id=p.block_id
            ORDER BY p.date,p.id""").fetchall()
        items = []
        for row in rows:
            if not include_archived and row["exam_status"] == "completed":
                continue
            if current_day_only and row["exam_date"] < date.today().isoformat():
                continue
            items.append({**dict(row), "key": f"study:{row['id']}", "source": "study", "priority": "normal",
                "title": f"{row['block_name']} · {row['task_type']}", "notes": row["resource"] or ""})
        for row in conn.execute("SELECT * FROM personal_tasks"):
            items.append({**dict(row), "key": f"manual:{row['id']}", "source": "manual", "subject_name": "个人事项",
                "block_name": row["title"], "task_type": "手动事项", "subject_id": None})
    rank = {"high": 0, "normal": 1, "low": 2}
    return sorted(items, key=lambda item: (item["date"], rank[item["priority"]], item["source"], item["id"]))


def summary(items):
    done = sum(item["status"] == "完成" for item in items)
    return {"total": len(items), "completed": done, "remaining": len(items)-done,
            "percent": round(done*100/len(items)) if items else None,
            "total_minutes": sum(max(0, item["est_minutes"] or 0) for item in items)}


def day_plan(day=None):
    day = valid_date(day) if day else date.today().isoformat()
    items = [item for item in all_tasks(current_day_only=day >= date.today().isoformat()) if item["date"] == day]
    stats = summary(items)
    return {"found": bool(items), "date": day, "title": "每日任务与复习计划",
            "total_minutes": stats["total_minutes"], "completed_count": stats["completed"], "task_items": items,
            "tasks": [f"[{item['status']}] {item['subject_name']} · {item['title']}（{item['est_minutes']}分钟）" for item in items]}


def overview(include_archived=False):
    items = all_tasks(include_archived=include_archived)
    subjects = study_db.get_subject_overview()
    if not include_archived:
        subjects = [subject for subject in subjects if subject["exam_status"] != "completed"]
    dates = [item["date"] for item in items] + [subject["exam_date"] for subject in subjects if subject['exam_date']]
    return {"tasks": items, "subjects": subjects, "summary": summary(items),
            "start_date": min(dates) if dates else None, "end_date": max(dates) if dates else None}
