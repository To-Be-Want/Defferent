# -*- coding: utf-8 -*-
"""
scheduler.py —— 排期算法（阶段3，系统的"心脏"）
放置位置：Co-Sight 项目根目录（和 study_db.py 同一层）
运行自检：python scheduler.py --test
生成真实计划：python scheduler.py --daily 240   （240=每天可用分钟数）

纯确定性代码，不调用大模型。职责（计划书 4.3）：
  1. 拓扑排序保证知识块依赖顺序
  2. 每科考前预留1天：仿真卷+冲刺
  3. 多科按"紧迫度=剩余工作量÷剩余天数"交错分配每日时长
  4. 遗忘曲线：有效掌握度 = 掌握度 × e^(-经过天数/记忆强度常数)，跌破阈值自动插入复习
  5. 排不下时不硬塞：报告缺口（供降级协商）
"""

import math
import argparse
from datetime import date, datetime, timedelta

import study_db

# ---------------- 可调参数（计划书定稿值） ----------------
MASTERY_PASS = 60          # 掌握度达标线
FORGET_ALERT = 50          # 有效掌握度跌破此值 → 插入复习任务
QUIZ_MINUTES = 15          # 每个知识块随堂小测的固定耗时
MOCK_EXAM_MINUTES = 120    # 考前仿真卷+冲刺的预留耗时
STABILITY_DAYS = 3.0       # 遗忘曲线基准：记忆强度1.0时约3天衰减到37%
PER_QUESTION_MINUTES = 6   # 真题练习：单题预估耗时（分钟）
MAX_PAPER_TASKS_PER_SUBJECT = 3  # 每科最多拆出的真题练习任务组数


def effective_mastery(mastery, memory_strength, last_reviewed_at, today=None):
    """有效掌握度 = 掌握度 × e^(-经过天数 / (记忆强度×基准天数))"""
    if not last_reviewed_at or mastery <= 0:
        return mastery
    today = today or date.today()
    last = datetime.fromisoformat(last_reviewed_at).date()
    days = max(0, (today - last).days)
    s = max(0.5, memory_strength) * STABILITY_DAYS
    return mastery * math.exp(-days / s)


def value_score(block_id, conn):
    """性价比分 = Σ(分值) × 考频权重 ÷ 预估耗时。用于降级取舍与薄弱池排序。"""
    rows = conn.execute(
        "SELECT score_value, frequency FROM exam_profile WHERE block_id=?",
        (block_id,)).fetchall()
    total_score, freq_w = 0.0, 0.5  # 无画像时给保底权重
    for r in rows:
        total_score += r["score_value"] or 0
        f = r["frequency"] or ""
        if "未出现" in f:
            continue
        # 从"3年2考"这类描述中抠出比率；抠不出按1.0
        import re
        m = re.match(r"(\d+)年(\d+)考", f)
        freq_w = max(freq_w, (int(m.group(2)) / int(m.group(1))) if m else 1.0)
    est = conn.execute("SELECT est_minutes FROM blocks WHERE id=?",
                       (block_id,)).fetchone()["est_minutes"] or 60
    return round((total_score if total_score else 5.0) * freq_w / (est / 60), 2)


def topo_sort(blocks):
    """按前置依赖做拓扑排序（Kahn算法），依赖数据有环时按原顺序兜底。"""
    ids = {b["id"] for b in blocks}
    indeg, children = {}, {}
    for b in blocks:
        pres = [int(x) for x in (b["prereq_ids"] or "").split(",")
                if x.strip().isdigit() and int(x) in ids]
        indeg[b["id"]] = len(pres)
        for p in pres:
            children.setdefault(p, []).append(b["id"])
    order, queue = [], [b["id"] for b in blocks if indeg[b["id"]] == 0]
    by_id = {b["id"]: b for b in blocks}
    while queue:
        cur = queue.pop(0)
        order.append(by_id[cur])
        for ch in children.get(cur, []):
            indeg[ch] -= 1
            if indeg[ch] == 0:
                queue.append(ch)
    return order if len(order) == len(blocks) else list(blocks)


def collect_tasks(conn, today, subject_id=None):
    """收集每科待排任务队列（已拓扑排序）。返回 {subject_id: [task,...]}
    每科队列顺序 = 讲解任务(拓扑序) → 真题练习(考频降序) → 回攻(遗忘曲线)。
    真题练习任务来自 exam_questions 表：按知识点分组，考频高的组排前面，
    并携带 resource/paper_ref 供资源匹配展示（如"私有:真题2024"）。

    已考完或日期已过的科目不自动排期，不擅自延期。"""
    result = {}
    subject_sql = "SELECT * FROM subjects"
    subject_args = []
    if subject_id is not None:
        subject_sql += " WHERE id=?"
        subject_args.append(subject_id)
    for s in conn.execute(subject_sql, subject_args).fetchall():
        if not s['exam_date']:
            continue  # Parsed resources can exist before an exam date is supplied.
        exam = date.fromisoformat(s["exam_date"])
        if s["exam_status"] == "completed" or exam <= today:
            continue
        blocks = conn.execute(
            "SELECT * FROM blocks WHERE subject_id=? AND status NOT IN ('跳过','已归档')",
            (s["id"],)).fetchall()
        tasks = []
        for b in topo_sort(blocks):
            eff = effective_mastery(b["mastery"], b["memory_strength"],
                                    b["last_reviewed_at"], today)
            if b["status"] == "未学":
                minutes = b["est_minutes"] + QUIZ_MINUTES
                # 用户开场说"带过"的块：讲解时长打4折
                if b["user_prior"] == "带过":
                    minutes = int(b["est_minutes"] * 0.4) + QUIZ_MINUTES
                tasks.append(dict(block_id=b["id"], name=b["name"],
                                  task_type="讲解", minutes=minutes,
                                  subject_id=s["id"], resource="",
                                  paper_ref=""))
            elif b["status"] == "已学" and eff < FORGET_ALERT:
                tasks.append(dict(block_id=b["id"], name=b["name"],
                                  task_type="回攻",
                                  minutes=int(b["est_minutes"] * 0.5) + QUIZ_MINUTES,
                                  subject_id=s["id"], resource="",
                                  paper_ref=""))
            elif b["status"] == "薄弱池":
                # 补救失败的知识点仍必须出现在后续计划，而不是永久消失。
                tasks.append(dict(block_id=b["id"], name=b["name"],
                                  task_type="薄弱回攻",
                                  minutes=int(b["est_minutes"] * 0.6) + QUIZ_MINUTES,
                                  subject_id=s["id"], resource="",
                                  paper_ref=""))
        # —— 真题练习任务：按知识点考频降序插入（排在讲解之后、回攻之前）——
        paper_tasks = _build_paper_tasks(conn, s["id"])
        if paper_tasks:
            insert_at = len(tasks)
            for i, t in enumerate(tasks):
                if t["task_type"] == "回攻":
                    insert_at = i
                    break
            tasks[insert_at:insert_at] = paper_tasks
        if not blocks and not paper_tasks:
            continue  # A calendar-only exam does not imply a study knowledge base.
        result[s["id"]] = dict(subject=dict(s), exam=exam, tasks=tasks)
    return result


def _build_paper_tasks(conn, subject_id):
    """从 exam_questions 构建该科真题练习任务（按知识点分组，考频降序）。
    返回 task 列表，每个任务带 resource（"私有:真题<标题>"）与 paper_ref（题目id列表）。"""
    questions = conn.execute(
        """SELECT q.id, q.knowledge_point, q.stem, p.title
           FROM exam_questions q LEFT JOIN past_papers p ON p.id = q.paper_id
           WHERE q.subject_id=? AND (p.parse_status IS NULL OR p.parse_status!='已归档') ORDER BY q.id""", (subject_id,)).fetchall()
    if not questions:
        return []
    groups = {}  # knowledge_point -> [q,...]
    for q in questions:
        kp = (q["knowledge_point"] or "综合").strip() or "综合"
        groups.setdefault(kp, []).append(q)
    ordered = sorted(groups.items(), key=lambda kv: len(kv[1]), reverse=True)
    tasks = []
    for kp, qs in ordered[:MAX_PAPER_TASKS_PER_SUBJECT]:
        title = qs[0]["title"] or "真题"
        qids = ",".join(str(q["id"]) for q in qs)
        tasks.append(dict(
            block_id=None, name=f"真题练习·{kp}", task_type="真题练习",
            minutes=min(len(qs) * PER_QUESTION_MINUTES, 120),
            subject_id=subject_id,
            resource=f"私有:真题《{title}》",
            paper_ref=f"真题题目ID:{qids}"))
    return tasks


def build_plan(daily_minutes=240, trigger="手动生成", description="",
               today=None, verbose=True, subject_id=None, dry_run=False):
    """核心入口：生成新版本计划写入 plans 表。返回 (版本号, 缺口列表)。"""
    today = today or date.today()
    conn = study_db.get_conn()

    queues = collect_tasks(conn, today, subject_id=subject_id)
    past_hints = [row["name"] for row in conn.execute(
        "SELECT id,name FROM subjects WHERE exam_status='pending' AND exam_date!='' AND exam_date<?", (today.isoformat(),))
        if subject_id is None or row["id"] == subject_id]
    if not queues:
        conn.close()
        hint = "；请在考试排期中确认已考完或修改日期：" + "、".join(past_hints) if past_hints else ""
        raise RuntimeError("暂无可排期的课程，请先设置未来考试日期并建立知识块或导入真题" + hint)

    # 新版本号
    row = conn.execute("SELECT MAX(version) AS v FROM plans").fetchone()
    v_before = row["v"] or 0
    version = v_before + 1

    plan_rows = []   # (date, block_id, task_type, minutes, sid, resource, paper_ref)
    # 1. 先钉死每科考前一天的仿真卷
    for sid, q in queues.items():
        mock_day = q["exam"] - timedelta(days=1)
        if mock_day >= today:
            plan_rows.append((mock_day.isoformat(), None, "仿真卷",
                              MOCK_EXAM_MINUTES, sid,
                              f"私有:《{q['subject']['name']}》仿真卷+冲刺", ""))

    # 2. 逐天贪心分配：每天把时长分给"最紧迫"的科目
    cur_day = today
    last_exam = max(q["exam"] for q in queues.values())
    while cur_day < last_exam:
        # 当天已被仿真卷占用的时长
        used = sum(r[3] for r in plan_rows if r[0] == cur_day.isoformat())
        capacity = daily_minutes - used
        while capacity > 0:
            # 计算每科紧迫度 = 剩余分钟 / 剩余可用天数（只考虑还没考、还有任务的科）
            best_sid, best_urg = None, -1
            for sid, q in queues.items():
                if not q["tasks"] or cur_day >= q["exam"]:
                    continue
                days_left = max(1, (q["exam"] - cur_day).days - 1)  # 留出仿真日
                remain = sum(t["minutes"] for t in q["tasks"])
                urg = remain / days_left
                if urg > best_urg:
                    best_sid, best_urg = sid, urg
            if best_sid is None:
                break
            task = queues[best_sid]["tasks"][0]
            # 装得下就装；当天还是空的就允许放一个超容任务（保证进度不停摆）
            if task["minutes"] <= capacity or capacity == daily_minutes - used:
                queues[best_sid]["tasks"].pop(0)
                plan_rows.append((cur_day.isoformat(), task["block_id"],
                                  task["task_type"], task["minutes"], best_sid,
                                  task.get("resource", ""), task.get("paper_ref", "")))
                capacity -= task["minutes"]
            else:
                break
        cur_day += timedelta(days=1)

    # 3. 缺口检测：还剩任务没排进去的科目
    gaps = []
    for sid, q in queues.items():
        if q["tasks"]:
            gaps.append(dict(subject=q["subject"]["name"],
                             unscheduled=[t["name"] for t in q["tasks"]],
                             minutes=sum(t["minutes"] for t in q["tasks"])))

    if dry_run:
        conn.close()
        return {"rows": plan_rows, "gaps": gaps, "past_hints": past_hints,
                "daily_minutes": daily_minutes, "base_version": v_before}

    # 4. 写库：旧版本任务不删（留痕），新版本整体写入（带 subject_id/resource/paper_ref）
    for d, bid, ttype, minutes, sid, resource, paper_ref in plan_rows:
        conn.execute(
            """INSERT INTO plans(version, date, block_id, task_type, est_minutes,
                                 subject_id, resource, paper_ref)
               VALUES (?,?,?,?,?,?,?,?)""",
            (version, d, bid, ttype, minutes, sid, resource, paper_ref))
    conn.commit()
    conn.close()
    study_db.log_plan_event(trigger, description or f"生成计划v{version}",
                            v_before, version)

    if verbose:
        print_plan(version, today)
        if gaps:
            print("\n⚠️ 排期缺口（需触发降级协商）：")
            for g in gaps:
                print(f"  {g['subject']}：{len(g['unscheduled'])}个知识块共"
                      f"{g['minutes']}分钟排不下 -> {g['unscheduled'][:5]}...")
    return version, gaps, past_hints


def print_plan(version, today=None):
    """打印某版本计划（按天）。"""
    conn = study_db.get_conn()
    rows = conn.execute(
        """SELECT p.date, p.task_type, p.est_minutes,
                  p.resource, p.paper_ref,
                  COALESCE(b.name,'（全科仿真卷+冲刺）') AS bname,
                  COALESCE(s.name,'') AS sname
           FROM plans p
           LEFT JOIN blocks b ON b.id = p.block_id
           LEFT JOIN subjects s ON s.id = p.subject_id
           WHERE p.version=? ORDER BY p.date, p.id""", (version,)).fetchall()
    conn.close()
    print(f"\n========== 复习计划 v{version} ==========")
    cur = None
    for r in rows:
        if r["date"] != cur:
            cur = r["date"]
            print(f"\n📅 {cur}")
        total = f"{r['est_minutes']}分钟"
        res = f"（📎 {r['resource']}）" if r["resource"] else ""
        print(f"   [{r['task_type']}] {r['sname'] or ''} - {r['bname']}{res}（{total}）")
    print("\n===================================")


def generate_learning_path(subject_id=None, version=None, daily_minutes=240,
                           today=None):
    """生成方案式个性化学习路径（learning_path）：
    {title, total_duration, steps:[{order, topic, task_type, resource, paper_ref, duration, date}]}
    输入学情（掌握度/薄弱点）与私有资料（真题/资源引用），输出供快通道展示。
    subject_id 为空 → 覆盖全部科目；version 为空 → 先生成一份最新计划再读取。"""
    active_only = version is None
    today = today or date.today()
    conn = study_db.get_conn()
    if version is None:
        # 查看路径不应每次都产生新计划版本；只有从未排过期时才初始化一次。
        if subject_id is None:
            current = conn.execute("SELECT MAX(version) AS v FROM plans").fetchone()["v"]
        else:
            current = conn.execute(
                "SELECT MAX(version) AS v FROM plans WHERE subject_id=?", (subject_id,)
            ).fetchone()["v"]
        conn.close()
        if current is None:
            return {"learning_path": None, "gap": "尚未保存复习计划。请先预览生成计划并确认。"}
        else:
            version, gaps = current, []
        conn = study_db.get_conn()
    else:
        gaps = []
    rows = conn.execute(
        """SELECT p.date, p.task_type, p.est_minutes, p.resource, p.paper_ref,
                  p.subject_id, s.exam_status, s.exam_date,
                  COALESCE(b.name,'（全科仿真卷+冲刺）') AS bname,
                  COALESCE(s.name,'') AS sname,
                  COALESCE(b.mastery, -1) AS mastery
           FROM plans p
           LEFT JOIN blocks b ON b.id = p.block_id
           LEFT JOIN subjects s ON s.id = p.subject_id
           WHERE p.version=?""" + (" AND p.subject_id=?" if subject_id else "") +
        " ORDER BY p.date, p.id", ((version, subject_id) if subject_id else (version,))).fetchall()
    conn.close()
    if active_only:
        rows = [row for row in rows if row["exam_status"] == "pending" and row["exam_date"] >= today.isoformat()]
    if not rows:
        return {"learning_path": None, "gap": "暂无计划，请先上传资料生成计划"}

    steps, total = [], 0
    for i, r in enumerate(rows, 1):
        total += r["est_minutes"] or 0
        res = r["resource"] or (f"私有:《{r['sname']}》{r['bname']}")
        steps.append({
            "order": i,
            "topic": r["bname"],
            "task_type": r["task_type"],
            "resource": res,
            "paper_ref": r["paper_ref"] or "",
            "duration_min": r["est_minutes"] or 30,
            "date": r["date"],
            "mastery": r["mastery"],
        })
    title = "全科目复习计划" if not subject_id else f"{rows[0]['sname']}复习计划"
    for r in rows:
        if subject_id and r["subject_id"] == subject_id:
            title = f"{r['sname']}复习计划"
            break
    return {"learning_path": {
        "title": title,
        "version": version,
        "total_duration_min": total,
        "steps": steps,
        "gap": gaps or None,
    }}


# ---------------- 自检：python scheduler.py --test ----------------

def self_test():
    import os, tempfile
    # 用临时数据库做测试，不碰真实 study.db
    tmp = os.path.join(tempfile.gettempdir(), "study_test.db")
    if os.path.exists(tmp):
        os.remove(tmp)
    study_db.DB_PATH = tmp
    study_db.init_db()
    today = date(2026, 7, 7)

    # 科目A：3天后考，4个块（含依赖链）；科目B：6天后考，3个块
    sa = study_db.add_subject("测试-高数", "2026-07-10")
    a1 = study_db.add_block(sa, "极限", "极限概念", "", 60)
    a2 = study_db.add_block(sa, "极限", "极限计算", str(a1), 60)
    a3 = study_db.add_block(sa, "导数", "导数定义", str(a2), 60)
    a4 = study_db.add_block(sa, "导数", "求导法则", str(a3), 60)
    sb = study_db.add_subject("测试-离散", "2026-07-13")
    b1 = study_db.add_block(sb, "逻辑", "命题逻辑", "", 60)
    b2 = study_db.add_block(sb, "逻辑", "谓词逻辑", str(b1), 60)
    b3 = study_db.add_block(sb, "图论", "图基础", "", 60)

    version, gaps, _past = build_plan(daily_minutes=300, today=today, verbose=True,
                                      trigger="自检")

    conn = study_db.get_conn()
    rows = conn.execute("SELECT * FROM plans WHERE version=?",
                        (version,)).fetchall()

    # 断言1：没有任何任务排在对应科目考试日当天或之后
    exam = {sa: date(2026, 7, 10), sb: date(2026, 7, 13)}
    subj_of = {a1: sa, a2: sa, a3: sa, a4: sa, b1: sb, b2: sb, b3: sb}
    for r in rows:
        if r["block_id"]:
            assert date.fromisoformat(r["date"]) < exam[subj_of[r["block_id"]]], \
                "任务排到考试日之后了！"
    # 断言2：依赖顺序正确（极限概念的日期 <= 极限计算的日期）
    def day_of(bid):
        return min(date.fromisoformat(r["date"]) for r in rows
                   if r["block_id"] == bid)
    assert day_of(a1) <= day_of(a2) <= day_of(a3) <= day_of(a4), "拓扑顺序错误！"
    assert day_of(b1) <= day_of(b2), "拓扑顺序错误！"
    # 断言3：两科各有一张仿真卷，且都在考前一天
    mocks = [r for r in rows if r["task_type"] == "仿真卷"]
    assert len(mocks) == 2 and {r["date"] for r in mocks} == \
        {"2026-07-09", "2026-07-12"}, "仿真卷日期错误！"
    # 断言4：遗忘曲线——学过5天、掌握度80、强度1.0的块应触发回攻
    eff = effective_mastery(80, 1.0, "2026-07-02T10:00:00", today)
    assert eff < FORGET_ALERT, f"遗忘曲线计算异常 eff={eff}"
    conn.close()

    # 断言5：时间不够时能报缺口——每天只给60分钟重排一次
    version2, gaps2, _past2 = build_plan(daily_minutes=60, today=today, verbose=False,
                                         trigger="自检-缺口")
    assert gaps2, "缺口检测失效：时间明显不够却没报缺口"
    print(f"\n[OK] 缺口检测正常：每天60分钟时报告 {len(gaps2)} 科排不下")
    os.remove(tmp)
    print("=== 阶段3 自检全部通过 ✅ ===")


if __name__ == "__main__":
    # Windows 传统控制台默认 GBK，无法打印计划里的 emoji。
    import sys
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--test", action="store_true", help="用假数据自检算法")
    ap.add_argument("--daily", type=int, default=240, help="每天可用分钟数")
    args = ap.parse_args()
    if args.test:
        self_test()
    else:
        build_plan(daily_minutes=args.daily, trigger="命令行手动生成")
