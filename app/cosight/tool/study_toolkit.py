# -*- coding: utf-8 -*-
"""
study_toolkit.py —— 复习智能体工具包（阶段4核心）
放置位置：D:\\Co-Sight\\app\\cosight\\tool\\study_toolkit.py

把我们已经测试通过的三个零件（study_db / material_analyst / scheduler）
包装成 Co-Sight 智能体可以调用的工具函数。所有函数返回字符串（给大模型看的结果）。
"""

import os
import sys
from datetime import date, datetime

# 项目根目录（本文件在 app/cosight/tool/ 下，往上3层）
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import study_db      # noqa: E402
import scheduler     # noqa: E402
from study_preferences import get_preferences  # noqa: E402


def _resolve(path):
    """相对路径一律相对项目根目录解析（用户上传的文件在 upload_files 下）。"""
    from user_context import current_user, user_root, contained_path
    from pathlib import Path
    if current_user.get():
        root = user_root() / "upload_files"
        supplied = Path(str(path))
        if supplied.is_absolute():
            candidate = supplied.resolve()
            if not candidate.is_relative_to(root.resolve()):
                raise PermissionError("只能读取当前账号上传的资料")
            return str(candidate)
        # Accept an upload id, own filename or the authenticated download URL.
        from cosight_server.deep_research.services.study_library import material_path, list_materials
        candidate = material_path(str(path))
        if candidate:
            return str(contained_path(root, candidate.resolve().relative_to(root.resolve())))
        filename = str(path).replace("\\", "/").split("/")[-1]
        matches = [item for item in list_materials() if item["filename"] == filename]
        if len(matches) == 1:
            return _resolve(str(material_path(matches[0]["upload_id"]).resolve()))
        return str(contained_path(root, str(path)))
    if os.path.isabs(path):
        return path
    # 依次尝试：原样 / 根目录 / upload_files目录
    for cand in (path, os.path.join(ROOT, path),
                 os.path.join(ROOT, "upload_files", os.path.basename(path))):
        if os.path.exists(cand):
            return cand
    return path


def _plan_text(version, subject_id=None):
    """把某版本计划渲染成字符串（给大模型和用户看）。"""
    conn = study_db.get_conn()
    rows = conn.execute(
        """SELECT p.date, p.task_type, p.est_minutes,
                  COALESCE(b.name,'（全科仿真卷+冲刺）') AS bname,
                  COALESCE(s.name,'') AS sname
           FROM plans p
           LEFT JOIN blocks b ON b.id = p.block_id
           LEFT JOIN subjects s ON s.id = p.subject_id
           WHERE p.version=?""" + (" AND p.subject_id=?" if subject_id else "") +
        " ORDER BY p.date, p.id", ((version, subject_id) if subject_id else (version,))).fetchall()
    conn.close()
    lines, cur = [f"📋 复习计划 v{version}"], None
    for r in rows:
        if r["date"] != cur:
            cur = r["date"]
            lines.append(f"\n📅 {cur}")
        lines.append(f"  [{r['task_type']}] {r['sname']} {r['bname']}"
                     f"（{r['est_minutes']}分钟）")
    return "\n".join(lines)


class StudyToolkit:
    """复习智能体的全部专属工具。"""

    def build_from_textbook(self, subject: str, textbook: str = "",
                            exam_date: str = "") -> str:
        """教材/公共知识驱动建库：没课件/真题时，联网搜索教材大纲，
        基于公共知识生成知识块并入库，让排期仍有依据。textbook 可指定教材名（如"张宇18讲"），
        留空则按科目自动搜公共大纲。exam_date 留空则用已设置的日期或提示补充。"""
        try:
            import material_analyst as ma
            import re
            from datetime import date
            prefs = get_preferences()
            if not prefs["allow_cloud_model"]:
                return ("当前已开启“仅使用本地资料”：不会把内容发送给在线模型，"
                        "因此无法自动从公共资料建库。请上传课件 PDF/DOCX/PPTX，"
                        "或在学习设置中重新允许在线模型。")
            cfg = ma.sniff_llm_config()
            # 0) 确定考试日期
            sid = study_db.get_subject_id_by_name(subject)
            if not exam_date and sid:
                conn = study_db.get_conn()
                row = conn.execute("SELECT exam_date FROM subjects WHERE id=?",
                                   (sid,)).fetchone()
                conn.close()
                if row and row["exam_date"]:
                    exam_date = row["exam_date"]
            if not exam_date:
                return (f"请先告诉我《{subject}》的考试日期（如：9月1号），"
                        "或者直接给日期，我再帮你排。")
            # 1) 可选联网搜索教材/大纲；关闭时只让模型使用通用课程知识。
            query = f"{subject} {textbook} 教材目录 知识点 大纲" if textbook else \
                    f"{subject} 大学 教材 课程大纲 知识点 章节"
            search_text = ""
            try:
                if not prefs["allow_web_search"]:
                    raise RuntimeError("用户已关闭联网搜索")
                import threading
                from app.cosight.tool.search_toolkit import SearchToolkit
                result_box = {}

                def _do_search():
                    st = SearchToolkit()
                    res = st.search_duckduckgo(query, max_results=8)
                    result_box["res"] = res

                from contextvars import copy_context
                search_context = copy_context()
                th = threading.Thread(target=lambda: search_context.run(_do_search), daemon=True)
                th.start()
                th.join(timeout=8)  # 搜索最多等 8 秒，避免卡死对话
                # 线程已结束（拿到结果）才取；未结束说明超时，用公共知识兜底
                if th.is_alive():
                    print("[教材搜索] 超时（>8s），改用公共知识")
                res = result_box.get("res")
                snippets = []
                for r in (res or [])[:8]:
                    t = (r.get("title") or "")[:80]
                    b = (r.get("body") or r.get("description") or "")[:300]
                    if t or b:
                        snippets.append(f"- {t}\n  {b}")
                search_text = "\n".join(snippets)
            except Exception as e:
                print(f"[教材搜索] 搜索失败，改用纯公共知识: {e}")
            if not search_text:
                search_text = f"（未检索到具体教材，请基于《{subject}》通用教学大纲回答）"
            # 2) 用 BLOCKS_PROMPT 生成知识块（材料=搜索摘要）
            prompt = ma.BLOCKS_PROMPT.format(subject=subject, text=search_text)
            raw = ma.chat([{"role": "user", "content": prompt}], cfg)
            blocks = ma.parse_json_from_llm(raw)
            if not blocks:
                return f"没能从公共资料里生成《{subject}》的知识块，请稍后重试或上传课件PDF。"
            # 3) 入库（复用 analyze 的写库逻辑：科目已存在则复用 id）
            study_db.init_db()
            if sid:
                s_actual = sid
            else:
                s_actual = study_db.add_subject(subject, exam_date)
            index_to_dbid = {}
            for i, b in enumerate(blocks):
                prereq = ",".join(str(index_to_dbid[j]) for j in b.get("prereq", [])
                                  if j in index_to_dbid)
                bid = study_db.add_block(s_actual, b.get("chapter", ""),
                                         b["name"], prereq,
                                         int(b.get("est_minutes", 60)))
                index_to_dbid[i] = bid
            return (f"✅ 《{subject}》公共知识库已建立：拆出 {len(blocks)} 个知识块"
                    f"（来源：{'教材《' + textbook + '》' if textbook else '联网公共大纲'}）。\n"
                    f"考试日期：{exam_date}。现在可以说「生成复习计划」来排期；"
                    "后续上传真题还可按考频优化排序。")
        except Exception as e:
            return f"教材建库失败：{e}"

    def parse_study_material(self, subject: str, exam_date: str,
                             courseware_path: str, paper_path: str = "") -> str:
        """解析课件/往年卷，建立知识块库与题型画像。"""
        try:
            import material_analyst as ma
            if not get_preferences()["allow_cloud_model"]:
                return ("当前已关闭在线模型。资料已留在本机，但自动拆知识块/题型画像需要模型分析；"
                        "请在学习设置中允许在线模型后重试。")
            cfg = ma.sniff_llm_config()
            cw = _resolve(courseware_path)
            if not os.path.exists(cw):
                return f"错误：找不到课件文件 {courseware_path}。请确认用户已上传，并把完整文件名告诉我。"
            pp = _resolve(paper_path) if paper_path else None
            if paper_path and not os.path.exists(pp):
                pp = None
            ma.analyze(subject, exam_date, [cw], pp, cfg)
            conn = study_db.get_conn()
            n = conn.execute(
                "SELECT COUNT(*) AS c FROM blocks b JOIN subjects s ON s.id=b.subject_id "
                "WHERE s.name=?", (subject,)).fetchone()["c"]
            conn.close()
            return (f"✅ 《{subject}》解析完成：拆出 {n} 个知识块，题型画像已建立，"
                    f"考试日期 {exam_date}。"
                    f"{'（未提供往年卷，画像为课件估计，建议补充）' if not pp else ''}"
                    f"下一步可调用 generate_study_plan 生成复习计划。")
        except Exception as e:
            return f"解析失败：{e}"

    def generate_study_plan(self, daily_minutes: int = None,
                            reason: str = "用户请求生成计划", subject: str = "") -> str:
        """生成/重排复习计划（确定性排期算法）。返回完整计划文本。"""
        try:
            if daily_minutes is None:
                daily_minutes = get_preferences()["default_daily_minutes"]
            daily_minutes = int(daily_minutes)
            if not 15 <= daily_minutes <= 1440:
                return "每天可用复习时长必须在 15 到 1440 分钟之间。"
            subject_id = None
            if subject and subject != "全部":
                subject_id = study_db.get_subject_id_by_name(subject)
                if not subject_id:
                    return f"还没有《{subject}》的知识库。请先上传该科资料，或告诉我考试日期后联网建立知识块。"
            version, gaps, past_hints = scheduler.build_plan(
                daily_minutes=daily_minutes, trigger=reason,
                description=reason, verbose=False, subject_id=subject_id)
            text = _plan_text(version, subject_id)
            text += f"\n\n⏱️ 本次按每天 {daily_minutes} 分钟可用时间排期。"
            if past_hints:
                text += ("\n\n以下课程的考试日期已过，本次未自动排期。"
                         "请在考试排期中标记已考完或修改日期："
                         + "、".join(past_hints))
            if gaps:
                text += "\n\n⚠️ 时间缺口警告（应向用户说明并协商取舍方案，方案由用户拍板）："
                for g in gaps:
                    text += (f"\n  《{g['subject']}》有{len(g['unscheduled'])}个知识块"
                             f"共约{g['minutes']}分钟排不进考前时间："
                             f"{'、'.join(g['unscheduled'][:6])}等")
            return text
        except Exception as e:
            return f"排期失败：{e}"

    def get_today_tasks(self) -> str:
        """Use the same task data as the homepage, including personal items."""
        try:
            from cosight_server.deep_research.services.study_plans import day_plan
            data = day_plan()
            lines = [f"🗓️ 今日任务（{data['date']}）"]
            lines.extend(data["tasks"] or ["今天尚未安排任务。可在计划总览中添加事项，或先生成课程复习计划。"])
            lines.append(f"已完成 {data['completed_count']}/{len(data['task_items'])} 项，预计共 {data['total_minutes']} 分钟。")
            return "\n".join(lines)
        except Exception as error:
            return f"获取任务卡失败：{error}"



    def record_quiz_score(self, block_id: int, score_rate: float) -> str:
        """记录某知识块的小测得分率(0~100)，更新掌握度，判断是否需要补救或重排计划。"""
        try:
            block_id, score_rate = int(block_id), float(score_rate)
            if not 0 <= score_rate <= 100:
                return "成绩必须在 0 到 100 之间，未写入学习记录。"
            old, new = study_db.update_mastery(block_id, score_rate)
            conn = study_db.get_conn()
            b = conn.execute("SELECT * FROM blocks WHERE id=?",
                             (block_id,)).fetchone()
            msg = (f"📊 已记录小测：《{b['name']}》得分率{score_rate:.0f}%，"
                   f"掌握度 {old:.0f} → {new:.0f}。")
            if b['status'] == '已归档':
                conn.close()
                return msg + "该资料已更正分类，成绩仅保留为历史记录，不恢复旧考点或修改计划。"
            # 达标判定依据：本次小测得分率（计划书：正确率>=60达标）
            if score_rate >= scheduler.MASTERY_PASS:
                conn.close()
                return msg + "✅ 达标，可以进入下一个知识块。"
            # 不达标：区分是否还有补救额度
            if b["remedial_used"] == 0:
                conn.execute("UPDATE blocks SET remedial_used=1 WHERE id=?",
                             (block_id,))
                conn.commit()
                conn.close()
                return (msg + "❌ 未达标（<60）。应立即进行一轮补救：换角度重新讲解该知识块"
                        "并针对错题解析，然后换一批题补测（仅此一轮）。")
            # 补救也失败 → 入薄弱池 + 自动重排
            conn.execute("UPDATE blocks SET status='薄弱池' WHERE id=?", (block_id,))
            conn.commit()
            conn.close()
            from learning_agent.proposals import quiz_advice
            return msg + "❌ 补救后仍未达标，已标记薄弱知识点。\n" + quiz_advice(b)
        except Exception as e:
            return f"记录成绩失败：{e}"

    def generate_quiz(self, block_id: int, kind: str = "随堂",
                      num_questions: int = 4) -> str:
        """出题官：按题型画像为知识块生成小测，返回在线答题页链接。"""
        try:
            import uuid, json as _json
            import material_analyst as ma
            conn = study_db.get_conn()
            b = conn.execute("SELECT * FROM blocks WHERE id=?", (block_id,)).fetchone()
            if not b:
                conn.close()
                return f"错误：知识块 {block_id} 不存在"
            profiles = conn.execute(
                "SELECT q_type, score_value, frequency FROM exam_profile WHERE block_id=?",
                (block_id,)).fetchall()
            prof_desc = "；".join(f"{p['q_type']}题约{p['score_value']}分({p['frequency']})"
                                  for p in profiles if p["q_type"]) or "无画像，出基础概念题"
            cfg = ma.sniff_llm_config()
            prompt = (f"你是应试出题专家。为《{b['name']}》(章节:{b['chapter']})出{num_questions}道"
                      f"{kind}小测题。该考点的真实题型画像：{prof_desc}。\n"
                      "要求：1)题型尽量贴合画像，但至少一半为可自动判分的选择/填空题 "
                      "2)选择题answer只写选项字母 3)只输出JSON数组：\n"
                      '[{"q_type":"选择","stem":"题干","options":["A. xx","B. xx","C. xx","D. xx"],'
                      '"answer":"A","explanation":"解析","score_value":5}]\n'
                      "填空题options为[]，answer为标准答案文本；计算/简答题同理(将由AI批改)。")
            from learning_agent.materials import course_context
            context = course_context(b['name'], subject_id=b['subject_id'])
            prompt += "\n优先依据以下上传课件，沿用其符号、解法及代码思路。资料不足明确标为补充练习。\n" + context
            raw = ma.chat([{"role": "user", "content": prompt}], cfg)
            questions = ma.parse_json_from_llm(raw)
            token = uuid.uuid4().hex[:12]
            cur = conn.execute(
                "INSERT INTO quizzes(block_id, kind, token, created_at) VALUES (?,?,?,datetime('now','localtime'))",
                (block_id, kind, token))
            qid = cur.lastrowid
            for q in questions:
                conn.execute(
                    """INSERT INTO questions(quiz_id,q_type,stem,options,answer,explanation,score_value)
                       VALUES (?,?,?,?,?,?,?)""",
                    (qid, q.get("q_type", "选择"), q.get("stem", ""),
                     _json.dumps(q.get("options", []), ensure_ascii=False),
                     str(q.get("answer", "")), q.get("explanation", ""),
                     float(q.get("score_value", 1))))
            conn.commit()
            conn.close()
            url = f"/quiz/{token}"  # Same-origin links work locally and behind a cloud HTTPS proxy.
            return (f"📝 《{b['name']}》{kind}小测已生成，共{len(questions)}题。"
                    f"答题链接：{url} （做完点交卷，系统自动判分并更新掌握度）")
        except Exception as e:
            return f"出题失败：{e}"

    def get_study_overview(self) -> str:
        """查看总体学习状况：各科进度、掌握度、薄弱池、重规划历史。"""
        try:
            conn = study_db.get_conn()
            lines = ["📈 学习总览"]
            for s in study_db.get_subject_overview():
                left = (date.fromisoformat(s["exam_date"]) - date.today()).days
                lines.append(f"《{s['name']}》：{s['block_count']}个知识块，"
                             f"平均掌握度{s['avg_mastery']}，距考试{left}天")
            weak = conn.execute(
                "SELECT name FROM blocks WHERE status='薄弱池'").fetchall()
            if weak:
                lines.append("🕳️ 薄弱池：" + "、".join(w["name"] for w in weak))
            evs = conn.execute(
                "SELECT time, trigger, description FROM plan_events "
                "ORDER BY id DESC LIMIT 5").fetchall()
            if evs:
                lines.append("🔄 最近重规划记录：")
                for e in evs:
                    lines.append(f"  {e['time']} [{e['trigger']}] {e['description']}")
            conn.close()
            return "\n".join(lines)
        except Exception as e:
            return f"获取总览失败：{e}"
        # ---------- 错题本工具 ----------
    def save_mistake(self, block_id: int, question: str, user_answer: str,
                     correct_answer: str, explanation: str = "") -> str:
        """保存错题到数据库"""
        try:
            from study_db import save_mistake as db_save
            # 如果 block_id 是 0，转为 None（数据库允许 NULL）
            if block_id == 0:
                block_id = None
            db_save(block_id, question, user_answer, correct_answer, explanation)
            return f"✅ 错题已保存：{question[:30]}..."
        except Exception as e:
            return f"保存错题失败：{e}"

    def get_mistakes(self, block_id: int = None) -> str:
        """获取所有错题，返回格式化文本"""
        try:
            from study_db import get_mistakes as db_get
            rows = db_get(block_id)
            if not rows:
                return "📭 还没有记录任何错题。"
            lines = ["📚 你的错题本："]
            for i, r in enumerate(rows[:20], 1):
                lines.append(f"{i}. 题目：{r['question'][:50]}...")
                lines.append(f"   你的答案：{r['user_answer'] or '未作答'}")
                lines.append(f"   正确答案：{r['correct_answer']}")
                lines.append(f"   解析：{r['explanation'] or '无'}")
                lines.append("")
            if len(rows) > 20:
                lines.append(f"... 还有 {len(rows)-20} 条，可以问我 '显示更多错题'")
            return "\n".join(lines)
        except Exception as e:
            return f"查询错题失败：{e}"

    # ---------------- 期末冲刺场景：真题入库 / 按科查计划 / 学习路径 ----------------

    def parse_exam_paper(self, subject: str, paper_path: str, year: str = "") -> str:
        """解析真题PDF：抽题入库（exam_questions），并统计知识点考频。期末冲刺核心。"""
        try:
            import material_analyst as ma
            path = _resolve(paper_path)
            if not os.path.exists(path):
                return f"错误：找不到真题文件 {paper_path}。请确认已上传到系统，并告诉我完整文件名。"
            cfg = ma.sniff_llm_config()
            text = ma.extract_pdf_text(path)
            if len(text.strip()) < 100:
                return "该文件识别到的文字太少，请检查图片或扫描件清晰度，也可上传文字版真题后再试。"
            sid = study_db.get_subject_id_by_name(subject)
            prompt = f"""你是试卷分析专家。下面是《{subject}》的一套真题文本。
请逐题提取，输出JSON数组（不要任何其他文字）：
[{{"knowledge_point":"对应知识点","q_type":"题型(选择/填空/计算/证明/简答)","stem":"题干","answer":"答案","explanation":"解析","score_value":分值}}]
要求：1)能识别多少题就提多少题；2)knowledge_point 尽量精炼（如"傅里叶变换"）；3)看不清答案的 answer 填""。
真题文本：
{text[:28000]}"""
            raw = ma.chat([{"role": "user", "content": prompt}], cfg, temperature=0.0)
            questions = ma.parse_json_from_llm(raw)
            if not questions:
                return "解析完成，但没有提取到可识别的题目（可能文本质量问题）。"
            pid = study_db.save_past_paper(sid, os.path.basename(path), year, "上传", path)
            n = study_db.save_exam_questions(pid, sid, questions)
            freq = study_db.get_knowledge_frequency(sid)
            freq_desc = "、".join(f"{f['knowledge_point']}考频{f['count']}次({f['pct']}%)"
                                  for f in freq[:6]) or "暂无"
            return (f"✅ 《{subject}》真题解析完成：登记 {n} 道题（来源：{os.path.basename(path)}）。\n"
                    f"📊 知识点考频：{freq_desc}\n"
                    "下一步可调用 generate_study_plan 生成复习计划，真题练习任务会自动按考频优先排。")
        except Exception as e:
            return f"真题解析失败：{e}"

    def get_plan_by_subject(self, subject: str) -> str:
        """按科目查看最新复习计划（期末冲刺：各科独立计划视图）。subject='全部' 看全部。
        科目无知识块但已有错题时 → 给出"错题优先基础计划"兜底，不直接报空。"""
        try:
            rows = study_db.get_plan_by_subject(version=None,
                                                subject_id=None if subject == "全部" else study_db.get_subject_id_by_name(subject))
            if not rows:
                # 兜底：该科目无排期计划但有错题 → 给错题优先基础计划
                if subject != "全部":
                    try:
                        from study_db import get_mistakes_by_subject
                        ms = get_mistakes_by_subject(subject, only_unmastered=True)
                        if ms:
                            lines = [f"📋 《{subject}》基础计划（暂未排期，先按错题优先）："]
                            for i, r in enumerate(ms[:6], 1):
                                lines.append(f"  {i}. 攻克错题：{r['question'][:40]}"
                                             f"{'…' if len(r['question']) > 40 else ''}")
                            if len(ms) > 6:
                                lines.append(f"  …还有 {len(ms)-6} 道未攻克")
                            lines.append("\n提示：上传课件/真题后即可排正式计划。现在可以先说「重做错题」把错题刷掉。")
                            return "\n".join(lines)
                    except Exception:
                        pass
                return "还没有生成过复习计划。请先上传课件/真题（parse_study_material / parse_exam_paper），再调用 generate_study_plan。"
            lines, cur = [f"📋 最新计划（{'全部科目' if subject == '全部' else subject}）"], None
            for r in rows:
                if r["date"] != cur:
                    cur = r["date"]
                    lines.append(f"\n📅 {cur}")
                res = f" 📎{r['resource']}" if r["resource"] else ""
                lines.append(f"  [{r['task_type']}] {r['sname']} {r['bname']}"
                             f"（{r['est_minutes']}分钟）{res}")
            return "\n".join(lines)
        except Exception as e:
            return f"查询计划失败：{e}"

    def generate_learning_path(self, subject: str = "全部") -> str:
        """生成方案式学习路径 learning_path（含资源引用与总时长）。用于"给我一个学习路径/按计划怎么学"。"""
        try:
            sid = None if subject == "全部" else study_db.get_subject_id_by_name(subject)
            if subject != "全部" and not sid:
                return f"未找到科目《{subject}》。当前科目：" + "、".join(
                    s["name"] for s in study_db.get_subject_overview()) or "（无科目，请先上传资料）"
            lp = scheduler.generate_learning_path(
                subject_id=sid,
                daily_minutes=get_preferences()["default_daily_minutes"],
            )
            path = lp.get("learning_path")
            if not path:
                return lp.get("gap") or "暂无学习路径。"
            lines = [f"🗺️ {path['title']}（计划v{path['version']}，总时长约"
                     f"{path['total_duration_min']}分钟）"]
            for st in path["steps"]:
                lines.append(f"  {st['order']}. [{st['date']}] {st['topic']}"
                             f"（{st['task_type']}，{st['duration_min']}分钟）")
                if st["resource"]:
                    lines.append(f"      📎 资源：{st['resource']}")
            if path.get("gap"):
                lines.append(f"\n⚠️ 时间缺口：{len(path['gap'])}科排不下（需协商取舍）")
            return "\n".join(lines)
        except Exception as e:
            return f"生成学习路径失败：{e}"

    def set_exam_date(self, subject: str, date_text: str) -> str:
        """设置/更新科目的考试日期。date_text 支持中文日期（9月1号/9月1日/2026年9月1日）
        或标准格式（2026-09-01 / 2026/9/1）。设置后即可生成复习计划。"""
        try:
            import re
            from datetime import date
            t = date_text.strip()
            year = date.today().year
            m = re.search(r"(20\d{2})[年/\-.]\s*(\d{1,2})[月/\-.]\s*(\d{1,2})", t)
            if m:
                year, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
            else:
                m = re.search(r"(\d{1,2})\s*[月/]\s*(\d{1,2})\s*[号日]?", t)
                if m:
                    mo, d = int(m.group(1)), int(m.group(2))
                else:
                    return (f"我没看懂日期「{date_text}」，请用类似「9月1号」或「2026-09-01」的格式告诉我。")
            exam = date(year, mo, d)
            if exam < date.today():
                exam = date(year + 1, mo, d)
            sid, created = study_db.set_subject_exam_date(subject, exam.isoformat())
            action = "已创建科目并设置" if created else "已更新"
            return (f"✅ 《{subject}》考试日期{action}为 {exam.isoformat()}。"
                    "现在可以说「生成复习计划」来排期，或上传课件/真题丰富计划内容。")
        except Exception as e:
            return f"设置考试日期失败：{e}"
