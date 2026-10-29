# -*- coding: utf-8 -*-
"""
study_db.py —— 复习智能体的数据层（阶段1）
放置位置：Co-Sight 项目根目录（和 requirements.txt 同一层）
运行自检：python study_db.py
作用：创建 study.db（SQLite 数据库文件）和全部数据表，并提供读写函数。
这是整个系统的"记忆"，所有智能体共享这里的数据。
"""

import sqlite3
import os
from threading import RLock
from datetime import datetime

# 数据库文件放在项目根目录下，名为 study.db
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "study.db")
_SCHEMA_LOCK = RLock()


def get_conn():
    """获取数据库连接。每次用完记得 close。"""
    from user_context import current_user, user_root, ClosingConnection
    path = str(user_root() / "study.db") if current_user.get() else DB_PATH
    conn = sqlite3.connect(path, factory=ClosingConnection)
    conn.row_factory = sqlite3.Row  # 让查询结果可以按列名访问
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")  # 并发写时最多等 5 秒，避免偶发 database is locked
    if current_user.get():
        with _SCHEMA_LOCK:
            if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='subjects'").fetchone():
                init_db(_connection=conn)
            elif _migrate_subjects(conn):
                conn.commit()
    return conn


def _migrate_subjects(conn):
    """Preserve existing courses; expired pending exams require user confirmation."""
    columns = {row[1] for row in conn.execute("PRAGMA table_info(subjects)")}
    if "exam_status" not in columns:
        conn.execute("ALTER TABLE subjects ADD COLUMN exam_status TEXT NOT NULL DEFAULT 'pending'")
        return True
    return False


def _migrate_mistakes(conn):
    """旧库兼容：为 mistakes 表补充新增列（幂等），并建知识点总结表。"""
    cols = {r[1] for r in conn.execute("PRAGMA table_info(mistakes)").fetchall()}
    adds = {
        "subject": "TEXT DEFAULT ''",
        "review_count": "INTEGER DEFAULT 0",
        "mastered": "INTEGER DEFAULT 0",
        "consecutive_correct": "INTEGER DEFAULT 0",
        "last_reviewed_at": "TEXT",
        "knowledge_points": "TEXT DEFAULT ''",
    }
    for col, ddl in adds.items():
        if col not in cols:
            conn.execute(f"ALTER TABLE mistakes ADD COLUMN {col} {ddl}")
    conn.execute("""
    CREATE TABLE IF NOT EXISTS mistake_summaries (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        subject TEXT NOT NULL,
        summary TEXT,
        created_at TEXT NOT NULL
    )""")


def _migrate_plans(conn):
    """旧库兼容：为 plans 表补充 subject_id/resource/paper_ref 列（幂等）。
    subject_id 用于"按学科独立查询计划"；resource/paper_ref 用于资源匹配（如"私有:PPT第3章"）。"""
    cols = {r[1] for r in conn.execute("PRAGMA table_info(plans)").fetchall()}
    adds = {
        "subject_id": "INTEGER REFERENCES subjects(id)",
        "resource": "TEXT DEFAULT ''",
        "paper_ref": "TEXT DEFAULT ''",
    }
    for col, ddl in adds.items():
        if col not in cols:
            conn.execute(f"ALTER TABLE plans ADD COLUMN {col} {ddl}")
    # 真题表（期末冲刺场景：真题入库 → 考频统计 → 排期安排真题练习）
    conn.execute("""
    CREATE TABLE IF NOT EXISTS past_papers (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        subject_id INTEGER REFERENCES subjects(id),
        title TEXT NOT NULL,                  -- 真题文件标题，如"2024回忆版真题"
        year TEXT DEFAULT '',                 -- 年份，如"2024"
        source TEXT DEFAULT '上传',            -- 上传/回忆版/官网
        file_path TEXT DEFAULT '',            -- 原始文件路径
        parse_status TEXT DEFAULT '未解析',    -- 未解析/已解析/解析失败
        created_at TEXT NOT NULL
    )""")
    conn.execute("""
    CREATE TABLE IF NOT EXISTS exam_questions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        paper_id INTEGER REFERENCES past_papers(id),
        subject_id INTEGER REFERENCES subjects(id),
        knowledge_point TEXT DEFAULT '',      -- 知识点标签，如"傅里叶变换"
        q_type TEXT DEFAULT '',               -- 题型：选择/填空/计算/证明...
        stem TEXT NOT NULL,                   -- 题干
        answer TEXT DEFAULT '',               -- 答案
        explanation TEXT DEFAULT '',          -- 解析
        score_value REAL DEFAULT 5
    )""")


def init_db(_connection=None):
    """创建全部数据表（已存在则跳过，重复运行安全）。"""
    if _connection is None:
        from user_context import current_user, user_root, ClosingConnection
        path = str(user_root() / "study.db") if current_user.get() else DB_PATH
        conn = sqlite3.connect(path, factory=ClosingConnection)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
    else:
        conn = _connection
    c = conn.cursor()

    # 1. 科目表
    c.execute("""
    CREATE TABLE IF NOT EXISTS subjects (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL UNIQUE,          -- 科目名，如"高等数学"；同名科目只能有一份知识库
        exam_date TEXT NOT NULL,            -- 考试日期，格式 2026-07-10
        credit REAL DEFAULT 1.0,            -- 学分权重
        daily_minutes INTEGER DEFAULT 240   -- 每天可用于该科的参考分钟数（排期会重算）
    )""")

    # 2. 知识块表（复习的最小单元）
    c.execute("""
    CREATE TABLE IF NOT EXISTS blocks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        subject_id INTEGER NOT NULL REFERENCES subjects(id),
        chapter TEXT,                       -- 所属章节，如"图论"
        name TEXT NOT NULL,                 -- 知识块名，如"最短路算法"
        prereq_ids TEXT DEFAULT '',         -- 前置知识块id，逗号分隔，如 "3,5"
        est_minutes INTEGER DEFAULT 60,     -- 预估学习耗时（分钟）
        mastery REAL DEFAULT 0,             -- 掌握度 0~100
        memory_strength REAL DEFAULT 1.0,   -- 记忆强度（遗忘曲线用，复习次数越多越大）
        last_reviewed_at TEXT,              -- 最近一次学习/复习时间
        status TEXT DEFAULT '未学',          -- 未学/已学/薄弱池/跳过
        user_prior TEXT DEFAULT '从头',      -- 从头/详讲/带过/跳过（开场问询的结果）
        remedial_used INTEGER DEFAULT 0     -- 是否已用掉补救额度（0/1）
    )""")

    # 3. 题型画像表（考点×题型×分值×考频）
    c.execute("""
    CREATE TABLE IF NOT EXISTS exam_profile (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        block_id INTEGER NOT NULL REFERENCES blocks(id),
        q_type TEXT NOT NULL,               -- 题型：选择/填空/计算/简答/证明...
        score_value REAL DEFAULT 0,         -- 预估分值
        frequency TEXT DEFAULT '',          -- 考频描述，如"3年2考"
        source TEXT DEFAULT '课件估计'       -- 依据：往年卷/大纲/课件估计
    )""")

    # 4. 计划表（每一行 = 某天的一个任务）
    c.execute("""
    CREATE TABLE IF NOT EXISTS plans (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        version INTEGER NOT NULL DEFAULT 1, -- 计划版本号，每次重规划+1
        date TEXT NOT NULL,                 -- 哪一天，格式 2026-07-07
        block_id INTEGER REFERENCES blocks(id),
        task_type TEXT NOT NULL,            -- 讲解/小测/补讲/补测/回攻/仿真卷
        est_minutes INTEGER DEFAULT 30,
        status TEXT DEFAULT '待办'           -- 待办/完成/未完成
    )""")

    # 5. 重规划日志表（答辩时展示"可追溯"的证据）
    c.execute("""
    CREATE TABLE IF NOT EXISTS plan_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        time TEXT NOT NULL,
        trigger TEXT NOT NULL,              -- 触发原因：小测不达标/用户改约束/遗忘告警/降级协商...
        description TEXT,
        version_before INTEGER,
        version_after INTEGER
    )""")

    # 6. 试卷表
    c.execute("""
    CREATE TABLE IF NOT EXISTS quizzes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        block_id INTEGER REFERENCES blocks(id),
        kind TEXT DEFAULT '随堂',            -- 随堂/补测/抽查/仿真
        token TEXT UNIQUE,                  -- 答题页地址用的随机码
        score REAL,                         -- 得分率 0~100，交卷后写入
        created_at TEXT,
        submitted_at TEXT
    )""")

    # 7. 题目表
    c.execute("""
    CREATE TABLE IF NOT EXISTS questions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        quiz_id INTEGER NOT NULL REFERENCES quizzes(id),
        q_type TEXT NOT NULL,               -- 选择/填空/计算/简答
        stem TEXT NOT NULL,                 -- 题干
        options TEXT DEFAULT '',            -- 选择题选项，JSON字符串，如 ["A. xx","B. xx"]
        answer TEXT NOT NULL,               -- 标准答案
        user_answer TEXT,
        is_correct INTEGER,                 -- 1/0，主观题由大模型批改后写入
        explanation TEXT,                   -- 解析/批改评语
        score_value REAL DEFAULT 1
    )""")
 
    # 8. 答疑记录表
    c.execute("""
    CREATE TABLE IF NOT EXISTS qa_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        block_id INTEGER REFERENCES blocks(id),
        question TEXT,
        answer TEXT,
        time TEXT
    )""")
    # 9. 错题本表（新增）
    c.execute("""
    CREATE TABLE IF NOT EXISTS mistakes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        block_id INTEGER REFERENCES blocks(id),   -- 关联知识块，可为空
        question TEXT NOT NULL,                  -- 题目原文
        user_answer TEXT,                        -- 用户的错误答案（新版统一不填，保留列兼容历史）
        correct_answer TEXT NOT NULL,            -- 正确答案
        explanation TEXT,                        -- 解析/讲解
        subject TEXT DEFAULT '',                 -- 科目（保存时自动识别）
        review_count INTEGER DEFAULT 0,          -- 重做次数
        mastered INTEGER DEFAULT 0,              -- 0未攻克/1已攻克
        consecutive_correct INTEGER DEFAULT 0,   -- 连续答对次数（连续2次标记攻克）
        last_reviewed_at TEXT,                   -- 最近重做时间
        knowledge_points TEXT DEFAULT '',        -- 知识点标签（学习计划时回填）
        created_at TEXT NOT NULL                 -- 记录时间
        ) """)
    # 10. 会话提问记录表（持久化：用户每次问的题都落库，
    #     后端重启后"保存这道题"仍能识别最近问过的题）
    c.execute("""
    CREATE TABLE IF NOT EXISTS asked_questions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id TEXT NOT NULL,                -- 学习会话ID
        question TEXT NOT NULL,                  -- 题目原文（非指令消息）
        created_at TEXT NOT NULL                 -- 提问时间
    )""")
    c.execute("CREATE INDEX IF NOT EXISTS idx_asked_sid ON asked_questions(session_id, id)")
    # 旧库兼容迁移 + 知识点总结表
    _migrate_subjects(conn)
    _migrate_mistakes(conn)
    # 旧库兼容：plans 表补列 + 真题表
    _migrate_plans(conn)
    conn.commit()
    if _connection is None:
        conn.close()
   
    
# ---------------- 常用读写函数（供智能体工具调用） ----------------

def add_subject(name, exam_date, credit=1.0):
    """创建科目；同名科目复用既有记录，避免同一门课被多次解析后分裂成多份数据。"""
    conn = get_conn()
    existing = conn.execute("SELECT id FROM subjects WHERE name=? ORDER BY id LIMIT 1",
                            (name,)).fetchone()
    if existing:
        conn.execute("UPDATE subjects SET exam_date=?, credit=? WHERE id=?",
                     (exam_date, credit, existing["id"]))
        conn.commit()
        sid = existing["id"]
        conn.close()
        return sid
    cur = conn.execute(
        "INSERT INTO subjects(name, exam_date, credit) VALUES (?,?,?)",
        (name, exam_date, credit))
    conn.commit()
    sid = cur.lastrowid
    conn.close()
    return sid


def set_subject_exam_date(subject, exam_date):
    """设置/更新科目的考试日期：科目已存在 → 更新 exam_date；不存在 → 创建科目。
    返回 (subject_id, 是否新建)。exam_date 需为 YYYY-MM-DD 格式（调用方负责解析）。"""
    conn = get_conn()
    row = conn.execute("SELECT id FROM subjects WHERE name=?", (subject,)).fetchone()
    if row:
        conn.execute("UPDATE subjects SET exam_date=?, exam_status='pending' WHERE id=?",
                     (exam_date, row["id"]))
        conn.commit()
        sid = row["id"]
        created = False
    else:
        cur = conn.execute(
            "INSERT INTO subjects(name, exam_date) VALUES (?,?)",
            (subject, exam_date))
        conn.commit()
        sid = cur.lastrowid
        created = True
    conn.close()
    return sid, created


# ---------------- 会话提问记录（持久化，供"保存这道题"识别） ----------------

def record_asked_question(session_id: str, question: str):
    """把用户问的一道题落库（同一会话同一题目去重，避免重复记录）。"""
    if not session_id or not question:
        return
    try:
        conn = get_conn()
        dup = conn.execute(
            "SELECT 1 FROM asked_questions WHERE session_id=? AND question=? LIMIT 1",
            (session_id, question)).fetchone()
        if not dup:
            conn.execute(
                "INSERT INTO asked_questions(session_id, question, created_at) VALUES (?,?,?)",
                (session_id, question, datetime.now().isoformat(timespec="seconds")))
            conn.commit()
        conn.close()
    except Exception as e:
        print(f"[record_asked_question] 落库失败: {e}")


def get_recent_asked_questions(session_id: str, limit: int = 10) -> list:
    """取某会话最近问过的题目（从新到旧）。后端重启后仍能识别。"""
    try:
        conn = get_conn()
        rows = conn.execute(
            "SELECT question FROM asked_questions WHERE session_id=? "
            "ORDER BY id DESC LIMIT ?", (session_id, limit)).fetchall()
        conn.close()
        return [r["question"] for r in rows]
    except Exception as e:
        print(f"[get_recent_asked_questions] 查询失败: {e}")
        return []
    conn.close()
    return sid, created


def add_block(subject_id, chapter, name, prereq_ids="", est_minutes=60):
    conn = get_conn()
    cur = conn.execute(
        """INSERT INTO blocks(subject_id, chapter, name, prereq_ids, est_minutes)
           VALUES (?,?,?,?,?)""",
        (subject_id, chapter, name, prereq_ids, est_minutes))
    conn.commit()
    bid = cur.lastrowid
    conn.close()
    return bid


def update_mastery(block_id, quiz_score_rate):
    """
    掌握度更新公式（计划书 5.3）：
    新掌握度 = 0.3 × 旧值 + 0.7 × 本次小测得分率(0~100)
    同时更新记忆强度和最近复习时间（遗忘曲线用）。
    返回 (旧值, 新值)。
    """
    conn = get_conn()
    row = conn.execute("SELECT mastery, memory_strength FROM blocks WHERE id=?",
                       (block_id,)).fetchone()
    if row is None:
        conn.close()
        raise ValueError(f"知识块 {block_id} 不存在")
    old = row["mastery"]
    new = round(0.3 * old + 0.7 * quiz_score_rate, 1)
    conn.execute(
        """UPDATE blocks SET mastery=?, memory_strength=memory_strength+0.5,
           last_reviewed_at=?, status=CASE WHEN status='已归档' THEN status ELSE '已学' END WHERE id=?""",
        (new, datetime.now().isoformat(timespec="seconds"), block_id))
    conn.commit()
    conn.close()
    return old, new


def get_subject_overview():
    """看板和任务卡用：返回每个科目的名称、考试日期、知识块总数、平均掌握度。"""
    conn = get_conn()
    rows = conn.execute("""
        SELECT s.id, s.name, s.exam_date, s.daily_minutes, s.exam_status,
               COUNT(b.id) AS block_count,
               ROUND(AVG(COALESCE(b.mastery,0)),1) AS avg_mastery
        FROM subjects s LEFT JOIN blocks b ON b.subject_id = s.id AND b.status!='已归档'
        GROUP BY s.id""").fetchall()
    conn.close()
    from datetime import date
    today = date.today().isoformat()
    return [{**dict(r), "display_status": "completed" if r["exam_status"] == "completed"
             else "unconfigured" if not r['exam_date']
             else "overdue" if r["exam_date"] < today else "pending"} for r in rows]


def update_subject_exam_dates(items):
    """Atomically add exams or update dates/status; never delete course resources."""
    if not isinstance(items, list):
        raise ValueError("课程排期必须是数组")
    if len(items) > 100:
        raise ValueError("一次最多保存100门考试")
    conn = get_conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        known = {row["id"]: dict(row) for row in conn.execute("SELECT * FROM subjects")}
        names = {row["name"].casefold() for row in known.values()}
        seen, changed = set(), False
        for item in items:
            if not isinstance(item, dict):
                raise ValueError("考试记录格式不正确")
            try:
                exam_date = datetime.strptime(str(item["exam_date"]), "%Y-%m-%d").date().isoformat()
                sid = int(item["id"]) if item.get("id") is not None else None
            except (KeyError, TypeError, ValueError):
                raise ValueError("课程编号或考试日期格式不正确") from None
            if sid is not None and (sid not in known or sid in seen):
                raise ValueError("课程编号不存在或重复")
            old = known.get(sid)
            status = item.get("exam_status", old["exam_status"] if old else "pending")
            if status not in ("pending", "completed"):
                raise ValueError("考试状态不正确")
            if old:
                seen.add(sid)
                changed |= (exam_date, status) != (old["exam_date"], old["exam_status"])
                conn.execute("UPDATE subjects SET exam_date=?, exam_status=? WHERE id=?", (exam_date, status, sid))
            else:
                name = item.get("name")
                if not isinstance(name, str) or not 1 <= len(name.strip()) <= 40:
                    raise ValueError("课程名须为1—40字")
                name = name.strip()
                if name.casefold() in names:
                    raise ValueError("已有同名课程，请修改已有考试；补考可使用不同名称")
                names.add(name.casefold())
                conn.execute("INSERT INTO subjects(name,exam_date,exam_status) VALUES (?,?,?)", (name, exam_date, status))
                changed = True
        conn.commit()
        return changed
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def log_plan_event(trigger, description, v_before, v_after):
    conn = get_conn()
    conn.execute(
        """INSERT INTO plan_events(time, trigger, description, version_before, version_after)
           VALUES (?,?,?,?,?)""",
        (datetime.now().isoformat(timespec="seconds"), trigger, description,
         v_before, v_after))
    conn.commit()
    conn.close()


# ---------------- 自检测试：python study_db.py ----------------

if __name__ == "__main__":
    print("=== 阶段1 自检开始 ===")
    init_db()

    # 插入一条测试科目和两个测试知识块
    sid = add_subject("测试科目-离散数学", "2026-07-10")
    b1 = add_block(sid, "图论", "图的基本概念", est_minutes=45)
    b2 = add_block(sid, "图论", "最短路算法", prereq_ids=str(b1), est_minutes=60)
    print(f"[OK] 插入科目 id={sid}，知识块 id={b1},{b2}")

    # 模拟一次小测：得分率 80 → 掌握度应从 0 变为 56.0 (0.3*0+0.7*80)
    old, new = update_mastery(b1, 80)
    assert new == 56.0, f"掌握度计算异常：期望 56.0，实际 {new}"
    print(f"[OK] 掌握度更新：{old} -> {new}（公式验证通过）")

    # 记录一条重规划日志
    log_plan_event("自检测试", "模拟触发重规划", 1, 2)
    print("[OK] 重规划日志写入成功")

    # 查询总览
    print("[OK] 科目总览：", get_subject_overview())

    # 清理测试数据，保持数据库干净
    conn = get_conn()
    conn.execute("DELETE FROM blocks WHERE subject_id=?", (sid,))
    conn.execute("DELETE FROM subjects WHERE id=?", (sid,))
    conn.execute("DELETE FROM plan_events WHERE trigger='自检测试'")
    conn.commit()
    conn.close()
    print("[OK] 测试数据已清理")
    print("=== 阶段1 自检全部通过 ✅ 可以进入阶段2 ===")
# ---------------- 错题本读写函数（新增） ----------------

def save_mistake(block_id, question, user_answer, correct_answer, explanation="", subject=""):
    """保存错题到数据库（subject 为自动识别的科目，可空）。
    block_id 为 0 或无关联知识块时转为 NULL，避免外键约束失败。"""
    if block_id == 0:
        block_id = None
    conn = get_conn()
    cur = conn.execute(
        """INSERT INTO mistakes(block_id, question, user_answer, correct_answer, explanation, subject, created_at)
           VALUES (?,?,?,?,?,?,?)""",
        (block_id, question, user_answer, correct_answer, explanation, subject, datetime.now().isoformat())
    )
    conn.commit()
    conn.close()
    return cur.lastrowid

def update_mistake_subject(mistake_id, subject):
    """更正某道错题的科目"""
    conn = get_conn()
    conn.execute("UPDATE mistakes SET subject=? WHERE id=?", (subject, mistake_id))
    conn.commit()
    conn.close()

def get_mistake_overview():
    """按科目汇总错题：科目、总数、已攻克数、未攻克数"""
    conn = get_conn()
    rows = conn.execute("""
        SELECT COALESCE(NULLIF(subject,''),'未分类') AS subj,
               COUNT(*) AS total,
               SUM(CASE WHEN mastered=1 THEN 1 ELSE 0 END) AS mastered_cnt
        FROM mistakes GROUP BY subj ORDER BY total DESC""").fetchall()
    conn.close()
    return [dict(r) for r in rows]

def get_mistakes_by_subject(subject=None, only_unmastered=False):
    """按科目获取错题；subject 为空返回全部；only_unmastered=True 只取未攻克。"""
    conn = get_conn()
    if subject:
        if only_unmastered:
            rows = conn.execute("""SELECT * FROM mistakes WHERE subject=? AND mastered=0
                                   ORDER BY created_at DESC""", (subject,)).fetchall()
        else:
            rows = conn.execute("""SELECT * FROM mistakes WHERE subject=?
                                   ORDER BY created_at DESC""", (subject,)).fetchall()
    else:
        if only_unmastered:
            rows = conn.execute("""SELECT * FROM mistakes WHERE mastered=0
                                   ORDER BY created_at DESC""").fetchall()
        else:
            rows = conn.execute("SELECT * FROM mistakes ORDER BY created_at DESC").fetchall()
    conn.close()
    return [dict(r) for r in rows]

def record_mistake_review(mistake_id, is_correct):
    """记录一次重做：重做次数+1；答对连续次数+1（达2次标记攻克）；答错清零。返回新状态。"""
    conn = get_conn()
    conn.execute("BEGIN IMMEDIATE")
    row = conn.execute("SELECT * FROM mistakes WHERE id=?", (mistake_id,)).fetchone()
    if row is None:
        conn.close()
        raise ValueError(f"错题 {mistake_id} 不存在")
    consec = row["consecutive_correct"] + 1 if is_correct else 0
    mastered = 1 if consec >= 2 else (row["mastered"] if is_correct else 0)
    conn.execute(
        """UPDATE mistakes SET review_count=review_count+1,
           consecutive_correct=?, mastered=?, last_reviewed_at=?
           WHERE id=?""",
        (consec, mastered, datetime.now().isoformat(timespec="seconds"), mistake_id))
    conn.commit()
    conn.close()
    return {"review_count": row["review_count"] + 1,
            "consecutive_correct": consec, "mastered": mastered}

def update_mistake_knowledge(mistake_id, knowledge_points):
    """回填错题的知识点标签（学习计划时）"""
    conn = get_conn()
    conn.execute("UPDATE mistakes SET knowledge_points=? WHERE id=?",
                 (knowledge_points, mistake_id))
    conn.commit()
    conn.close()

def save_mistake_summary(subject, summary):
    """保存某科目的错题知识点总结报告（供学习规划师读取）"""
    conn = get_conn()
    conn.execute("INSERT INTO mistake_summaries(subject, summary, created_at) VALUES (?,?,?)",
                 (subject, summary, datetime.now().isoformat()))
    conn.commit()
    conn.close()

def get_mistake_summaries(subject=None):
    """获取知识点总结报告；subject 为空返回全部（最新的排前面）"""
    conn = get_conn()
    if subject:
        rows = conn.execute("SELECT * FROM mistake_summaries WHERE subject=? ORDER BY id DESC",
                            (subject,)).fetchall()
    else:
        rows = conn.execute("SELECT * FROM mistake_summaries ORDER BY id DESC").fetchall()
    conn.close()
    return [dict(r) for r in rows]

def get_mistakes(block_id=None, limit=100):
    """获取错题列表，可按知识块筛选"""
    conn = get_conn()
    if block_id:
        rows = conn.execute(
            "SELECT * FROM mistakes WHERE block_id=? ORDER BY created_at DESC LIMIT ?",
            (block_id, limit)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM mistakes ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ---------------- 通用查询/更新函数（quiz_engine / mode_switch 等模块依赖） ----------------

def get_subject(subject_id):
    """获取科目记录（Row 对象，可按列名或索引访问）"""
    conn = get_conn()
    row = conn.execute("SELECT * FROM subjects WHERE id=?", (subject_id,)).fetchone()
    conn.close()
    return row


def get_blocks_by_subject(subject_id):
    """获取某科目的全部知识块"""
    conn = get_conn()
    rows = conn.execute("SELECT * FROM blocks WHERE subject_id=? AND status!='已归档' ORDER BY id",
                        (subject_id,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def update_block_mastery(block_id, mastery):
    """直接设置知识块掌握度（0~100）"""
    conn = get_conn()
    conn.execute("UPDATE blocks SET mastery=? WHERE id=?", (mastery, block_id))
    conn.commit()
    conn.close()


def update_block_status(block_id, status):
    """设置知识块状态：未学/已学/薄弱池/跳过"""
    conn = get_conn()
    conn.execute("UPDATE blocks SET status=? WHERE id=?", (status, block_id))
    conn.commit()
    conn.close()


def get_questions_by_quiz(quiz_id):
    """获取某次小测的全部题目（返回元组列表，兼容按索引访问的调用方）"""
    conn = get_conn()
    rows = conn.execute("SELECT * FROM questions WHERE quiz_id=? ORDER BY id",
                        (quiz_id,)).fetchall()
    conn.close()
    return [tuple(r) for r in rows]


def update_question_result(question_id, user_answer, is_correct):
    """记录单题作答结果（user_answer 与对错标记）"""
    conn = get_conn()
    conn.execute("UPDATE questions SET user_answer=?, is_correct=? WHERE id=?",
                 (user_answer, is_correct, question_id))
    conn.commit()
    conn.close()


# ---------------- 真题与按科计划（期末冲刺场景，方案 v3） ----------------

def save_past_paper(subject_id, title, year="", source="上传", file_path=""):
    """登记一份真题文件，返回 paper_id。subject_id 为 0 时转 None。"""
    if subject_id == 0:
        subject_id = None
    conn = get_conn()
    cur = conn.execute(
        """INSERT INTO past_papers(subject_id, title, year, source, file_path, created_at)
           VALUES (?,?,?,?,?,?)""",
        (subject_id, title, year, source, file_path, datetime.now().isoformat()))
    conn.commit()
    pid = cur.lastrowid
    conn.close()
    return pid


def save_exam_questions(paper_id, subject_id, questions):
    """批量写入真题题目。questions 为 [{"knowledge_point":..,"q_type":..,"stem":..,"answer":..,"explanation":..,"score_value":..}, ...]
    返回写入条数。"""
    if subject_id == 0:
        subject_id = None
    conn = get_conn()
    n = 0
    for q in questions:
        if not q.get("stem"):
            continue
        conn.execute(
            """INSERT INTO exam_questions(paper_id, subject_id, knowledge_point, q_type, stem, answer, explanation, score_value)
               VALUES (?,?,?,?,?,?,?,?)""",
            (paper_id, subject_id, q.get("knowledge_point", ""), q.get("q_type", ""),
             q["stem"], q.get("answer", ""), q.get("explanation", ""),
             float(q.get("score_value", 5))))
        n += 1
    conn.commit()
    conn.close()
    return n


def get_exam_questions_by_subject(subject_id=None, knowledge_point=None):
    """按科目/知识点取真题题目；subject_id 为空取全部。"""
    conn = get_conn()
    sql = "SELECT * FROM exam_questions WHERE paper_id NOT IN (SELECT id FROM past_papers WHERE parse_status='已归档')"
    args = []
    if subject_id:
        sql += " AND subject_id=?"
        args.append(subject_id)
    if knowledge_point:
        sql += " AND knowledge_point LIKE ?"
        args.append(f"%{knowledge_point}%")
    sql += " ORDER BY id"
    rows = conn.execute(sql, args).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_knowledge_frequency(subject_id=None):
    """统计各知识点的真题考频（出现次数+占比），供排期按考频加权。"""
    conn = get_conn()
    sql = ("SELECT COALESCE(NULLIF(knowledge_point,''),'未标注') AS kp, COUNT(*) AS cnt "
           "FROM exam_questions WHERE paper_id NOT IN (SELECT id FROM past_papers WHERE parse_status='已归档')")
    args = []
    if subject_id:
        sql += " AND subject_id=?"
        args.append(subject_id)
    sql += " GROUP BY kp ORDER BY cnt DESC"
    rows = conn.execute(sql, args).fetchall()
    conn.close()
    total = sum(r["cnt"] for r in rows) or 1
    return [{"knowledge_point": r["kp"], "count": r["cnt"],
             "pct": round(r["cnt"] * 100.0 / total, 1)} for r in rows]


def get_plan_by_subject(version=None, subject_id=None):
    """按科目查询某版本计划；version 为空取最新版；subject_id 为空取全部。
    返回带 resource/paper_ref 的任务行，用于"查看高数计划"等按科独立视图。"""
    active_only = version is None
    conn = get_conn()
    if version is None:
        # 计划版本是历史记录的全局流水号；但“查看某一科”必须取该科自己的
        # 最新版本，而不是恰好最后一次被重排的另一门课。
        if subject_id:
            row = conn.execute(
                "SELECT MAX(version) AS v FROM plans WHERE subject_id=?", (subject_id,)
            ).fetchone()
        else:
            row = conn.execute("SELECT MAX(version) AS v FROM plans").fetchone()
        version = row["v"] if row else None
        if version is None:
            conn.close()
            return []
    sql = ("""SELECT p.id, p.date, p.task_type, p.est_minutes, p.status,
                     p.resource, p.paper_ref, p.subject_id,
                     COALESCE(b.name,'（全科仿真卷+冲刺）') AS bname,
                     COALESCE(s.name,'') AS sname
              FROM plans p
              LEFT JOIN blocks b ON b.id = p.block_id
              LEFT JOIN subjects s ON s.id = p.subject_id
              WHERE p.version=?""")
    args = [version]
    if active_only:
        from datetime import date
        sql += " AND s.exam_status='pending' AND s.exam_date>=?"
        args.append(date.today().isoformat())
    if subject_id:
        sql += " AND p.subject_id=?"
        args.append(subject_id)
    sql += " ORDER BY p.date, p.id"
    rows = conn.execute(sql, args).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_subject_id_by_name(subject):
    """按科目名取 id（找不到返回 None），供意图识别后查计划。"""
    conn = get_conn()
    row = conn.execute("SELECT id FROM subjects WHERE name=? ORDER BY id LIMIT 1", (subject,)).fetchone()
    conn.close()
    return row["id"] if row else None
