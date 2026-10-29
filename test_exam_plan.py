# -*- coding: utf-8 -*-
"""v3 期末冲刺场景专项测试：真题入库→排期→按科查询→learning_path。
使用临时数据库，不碰真实 study.db。
运行：python test_exam_plan.py
"""
import os
import sys
import tempfile
from datetime import date

# Windows 传统控制台默认 GBK，无法打印测试结果中的 emoji；统一使用 UTF-8。
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import study_db
import scheduler

failures = []

def check(name, cond, detail=""):
    status = "✅" if cond else "❌"
    print(f"  {status} {name} {detail}")
    if not cond:
        failures.append(name)

def main():
    print("=== v3 期末冲刺场景测试 ===")
    tmp = os.path.join(tempfile.gettempdir(), "study_v3_test.db")
    if os.path.exists(tmp):
        os.remove(tmp)
    study_db.DB_PATH = tmp
    study_db.init_db()
    today = date(2026, 7, 7)

    # 1. 迁移幂等：init_db 重复运行安全（plans 已有 subject_id/resource/paper_ref）
    conn = study_db.get_conn()
    cols = {r[1] for r in conn.execute("PRAGMA table_info(plans)").fetchall()}
    check("plans 表含 subject_id", "subject_id" in cols)
    check("plans 表含 resource", "resource" in cols)
    check("plans 表含 paper_ref", "paper_ref" in cols)
    tcols = {r[1] for r in conn.execute("PRAGMA table_info(exam_questions)").fetchall()}
    check("exam_questions 表已建", "knowledge_point" in tcols and "stem" in tcols)
    conn.close()
    study_db.init_db()  # 幂等再跑一次
    check("init_db 幂等", True)

    # 2. 建科目 + 知识块
    sa = study_db.add_subject("高等数学", "2026-07-10")
    a1 = study_db.add_block(sa, "极限", "极限概念", "", 60)
    a2 = study_db.add_block(sa, "极限", "极限计算", str(a1), 60)
    sb = study_db.add_subject("线性代数", "2026-07-13")
    b1 = study_db.add_block(sb, "矩阵", "矩阵乘法", "", 60)
    check("建科目/知识块", sa and sb and a1 and b1)

    # 3. 真题入库（模拟 LLM 抽题结果）
    pid = study_db.save_past_paper(sa, "2024高数真题", "2024", "上传", "upload_files/2024高数真题.pdf")
    n = study_db.save_exam_questions(pid, sa, [
        {"knowledge_point": "极限", "q_type": "计算", "stem": "求 lim(x->0) sinx/x", "answer": "1", "score_value": 10},
        {"knowledge_point": "极限", "q_type": "计算", "stem": "求 lim(x->∞) (1+1/x)^x", "answer": "e", "score_value": 10},
        {"knowledge_point": "导数", "q_type": "计算", "stem": "求 y=x^2 的导数", "answer": "2x", "score_value": 8},
    ])
    check("真题入库 3 道", n == 3)
    pid2 = study_db.save_past_paper(sb, "2024线代真题", "2024")
    study_db.save_exam_questions(pid2, sb, [
        {"knowledge_point": "矩阵", "q_type": "计算", "stem": "矩阵乘法计算", "answer": "", "score_value": 10},
    ])
    check("线代真题入库", True)

    # 4. 考频统计（极限 2 次 = 66.7%）
    freq = study_db.get_knowledge_frequency(sa)
    check("考频统计", len(freq) >= 2 and freq[0]["knowledge_point"] == "极限"
          and freq[0]["count"] == 2 and abs(freq[0]["pct"] - 66.7) < 0.2, str(freq))

    # 5. 排期：真题练习任务应出现，且考频高的极限组排前面
    version, gaps, _past = scheduler.build_plan(daily_minutes=300, today=today,
                                                verbose=False, trigger="v3测试")
    conn = study_db.get_conn()
    rows = conn.execute(
        "SELECT * FROM plans WHERE version=? ORDER BY date, id", (version,)).fetchall()
    paper_tasks = [r for r in rows if r["task_type"] == "真题练习"]
    check("计划含真题练习任务", len(paper_tasks) >= 2, f"共{len(paper_tasks)}个")
    if paper_tasks:
        check("真题任务带 resource", paper_tasks[0]["resource"] != "",
              f"{paper_tasks[0]['resource']}")
        check("真题任务带 subject_id", paper_tasks[0]["subject_id"] == sa,
              f"sid={paper_tasks[0]['subject_id']}")
    # 每行都应有 subject_id（含仿真卷）
    no_sid = [r for r in rows if r["subject_id"] is None]
    check("所有计划行都有 subject_id", not no_sid, f"缺{len(no_sid)}行")
    # 仿真卷科目归属正确（高数考前1天）
    mocks = [r for r in rows if r["task_type"] == "仿真卷"]
    check("仿真卷带科目+资源", all(m["subject_id"] and m["resource"] for m in mocks),
          str([(m["subject_id"], m["resource"]) for m in mocks]))

    # 6. 按科独立查询
    ga_rows = study_db.get_plan_by_subject(version=version, subject_id=sa)
    gb_rows = study_db.get_plan_by_subject(version=version, subject_id=sb)
    check("按科查询高数", all(r["subject_id"] == sa for r in ga_rows) and len(ga_rows) > 0)
    check("按科查询线代", all(r["subject_id"] == sb for r in gb_rows) and len(gb_rows) > 0)
    all_rows = study_db.get_plan_by_subject(version=version)
    check("按科查询全部", len(all_rows) == len(ga_rows) + len(gb_rows))

    # 7. learning_path 输出
    lp = scheduler.generate_learning_path(subject_id=sa, version=version)
    path = lp.get("learning_path")
    check("learning_path 生成", path and path["title"].startswith("高等数学"),
          str(path["title"] if path else lp))
    if path:
        check("learning_path 含资源引用",
              any(s["resource"] for s in path["steps"]),
              str([s["resource"] for s in path["steps"]][:3]))
        check("learning_path 含真题练习",
              any(s["task_type"] == "真题练习" for s in path["steps"]))
        check("learning_path 总时长>0", path["total_duration_min"] > 0)

    # 8. 科目名 → id 查询（供意图识别）
    sid = study_db.get_subject_id_by_name("高等数学")
    check("get_subject_id_by_name", sid == sa, f"sid={sid}")

    conn.close()
    os.remove(tmp)
    print()
    if failures:
        print(f"=== 失败 {len(failures)} 项：{failures} ❌ ===")
        sys.exit(1)
    print("=== v3 期末冲刺场景测试全部通过 ✅ ===")

if __name__ == "__main__":
    main()
