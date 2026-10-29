# -*- coding: utf-8 -*-
"""
study_fast_channel.py —— 复习智能体快通道（阶段5）
支持智能保存题目/错题，自动提取上下文，多题选择，保存全部
"""

import json
import sys
import os
import re
import logging
from datetime import datetime
import requests

ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import material_analyst as ma
from app.cosight.tool.study_toolkit import StudyToolkit
from study_db import save_mistake as _db_save_mistake

_toolkit = StudyToolkit()

# ---------- 分诊规则 ----------
DEEP_TRIGGERS = ["解析", "上传的", "调研", "研究报告", "深度研究", "完整模式", "仿真卷"]
STUDY_KEYWORDS = ["学习", "复习", "任务", "计划", "知识块", "小测", "测验", "考试",
                  "掌握", "讲解", "讲一下", "教我", "薄弱", "进度", "总览", "学不完",
                  "出题", "成绩", "分数", "答疑", "今天学", "开始学", "下一个", "学什么",
                  "错题", "错题本", "错题记录", "查看", "记录", "保存", "存"]

SYSTEM_PROMPT = """你是一位期末突击复习助教，陪学生高效备考。语气自然、简洁、像聊天，绝不写报告。

工作守则：
1. 学生开始学习/问今天学什么 → 调 get_today_tasks 拿任务卡
2. 讲解知识块：紧扣考试怎么考，讲完主动问有没有疑问；学生没问题了 → 调 generate_quiz 出小测
3. 学生自由提问 = 答疑，直接答，不限次数
4. 学生说时间变了/学不完了 → 调 generate_study_plan 重排
5. 查进度 → get_study_overview
6. 工具返回里的"应..."字样是给你的行动指引，照做
7. 回复务必精炼，多用短句；一次只推进一小步，等学生回应
8. 【强制规则】当用户说“保存错题”或“存这道题”时，你必须**直接调用 save_mistake 工具**保存错题。如果题目信息不完整（缺少题目、错误答案、正确答案），请让用户补充。
9. 【强制规则】如果用户明确说了错题内容（例如“我算成了0”、“我做错了”），你应该解答后调用 save_mistake 工具保存。
10. 学生上传了真题（说"解析真题/分析真题"）→ 调 parse_exam_paper 抽题入库并统计考频
11. 学生问"查看XX计划/计划给我看看/我的计划" → 调 get_plan_by_subject（XX传科目名，不确定传"全部"）
12. 学生问"怎么学/学习路径/学习路线" → 调 generate_learning_path 给个性化学习路径
13. 【强制规则·考试日期】当学生告诉你某科考试日期（如"9月1号""9月1日""2026-09-01"）时，
    必须调 set_exam_date 把日期写入系统（科目名+日期文本原样传入），不要只回一句话！
    这通常发生在你问"哪天考XX"之后，学生只回日期——结合上文科目名调用。
14. 【上下文理解】学生经常只回短句（日期/科目名/编号）承接你的问题，要结合上文理解，
    不要把短回答当独立任务。
15. 【强制规则·无资料建库】当学生说"生成计划/复习计划"但该科目还没有知识块（题库空）时：
    a) 先问学生用的是什么教材（如"你高数用哪本教材？张宇18讲还是同济？"）
    b) 学生给了教材名 → 调 build_from_textbook（subject=科目, textbook=教材名）
    c) 学生说没有教材/不知道 → 调 build_from_textbook（只传 subject，自动联网搜公共大纲）
    d) 不要直接说"排不了"，先用公共知识把题库建起来再排。

当前日期：{today}"""

TOOLS = [
    {"type": "function", "function": {
        "name": "parse_study_material", "description": "解析当前用户上传的课件和可选往年卷，建立课程知识块",
        "parameters": {"type": "object", "properties": {
            "subject": {"type": "string"}, "exam_date": {"type": "string"},
            "courseware_path": {"type": "string"}, "paper_path": {"type": "string"}},
            "required": ["subject", "exam_date", "courseware_path"]}}},
    {"type": "function", "function": {
        "name": "get_today_tasks", "description": "获取今日任务卡",
        "parameters": {"type": "object", "properties": {}, "required": []}}},
    {"type": "function", "function": {
        "name": "get_study_overview", "description": "查看学习总览",
        "parameters": {"type": "object", "properties": {}, "required": []}}},
    {"type": "function", "function": {
        "name": "generate_study_plan", "description": "生成或重排复习计划",
        "parameters": {"type": "object", "properties": {
            "daily_minutes": {"type": "integer", "description": "每天可用分钟数；不填则使用主页考试排期中的时间段总和"},
            "reason": {"type": "string", "description": "重排原因"},
            "subject": {"type": "string", "description": "科目名；明确要求全部科目时才留空"}},
            "required": []}}},
    {"type": "function", "function": {
        "name": "generate_quiz", "description": "为某知识块生成随堂小测",
        "parameters": {"type": "object", "properties": {
            "block_id": {"type": "integer", "description": "知识块id"},
            "kind": {"type": "string", "description": "随堂/补测/抽查，默认随堂"}},
            "required": ["block_id"]}}},
    {"type": "function", "function": {
        "name": "record_quiz_score", "description": "手动记录小测得分率",
        "parameters": {"type": "object", "properties": {
            "block_id": {"type": "integer"},
            "score_rate": {"type": "number", "description": "0~100"}},
            "required": ["block_id", "score_rate"]}}},
    {"type": "function", "function": {
        "name": "save_mistake",
        "description": "保存错题到数据库",
        "parameters": {
            "type": "object",
            "properties": {
                "block_id": {"type": "integer", "description": "关联的知识块ID，不确定填0"},
                "question": {"type": "string", "description": "题目的完整描述"},
                "user_answer": {"type": "string", "description": "用户给出的错误答案"},
                "correct_answer": {"type": "string", "description": "正确答案"},
                "explanation": {"type": "string", "description": "解题思路/解析"}
            },
            "required": ["question", "correct_answer"]
        }}},
    {"type": "function", "function": {
        "name": "get_mistakes",
        "description": "查看已保存的所有错题",
        "parameters": {"type": "object", "properties": {
            "block_id": {"type": "integer", "description": "可选，只查看某个知识块的错题"}
        }, "required": []}}},
    {"type": "function", "function": {
        "name": "parse_exam_paper",
        "description": "解析上传的真题PDF：抽题入库并统计知识点考频，供排期按考频优先安排真题练习",
        "parameters": {"type": "object", "properties": {
            "subject": {"type": "string", "description": "科目名，如 高等数学"},
            "paper_path": {"type": "string", "description": "真题文件名/路径"},
            "year": {"type": "string", "description": "年份，可选"}},
            "required": ["subject", "paper_path"]}}},
    {"type": "function", "function": {
        "name": "get_plan_by_subject",
        "description": "按科目查看最新复习计划；传'全部'查看全部科目（期末冲刺：各科独立计划视图）",
        "parameters": {"type": "object", "properties": {
            "subject": {"type": "string", "description": "科目名或'全部'"}},
            "required": ["subject"]}}},
    {"type": "function", "function": {
        "name": "generate_learning_path",
        "description": "生成个性化学习路径（含资源引用与总时长），用于回答'怎么学/学习路径'",
        "parameters": {"type": "object", "properties": {
            "subject": {"type": "string", "description": "科目名或'全部'，默认全部"}},
            "required": []}}},
    {"type": "function", "function": {
        "name": "set_exam_date",
        "description": "设置/更新某科目的考试日期（支持'9月1号'等中文日期）。学生告知考试日期时调用",
        "parameters": {"type": "object", "properties": {
            "subject": {"type": "string", "description": "科目名，如 高等数学"},
            "date_text": {"type": "string", "description": "日期原文，如 '9月1号' 或 '2026-09-01'"}},
            "required": ["subject", "date_text"]}}},
    {"type": "function", "function": {
        "name": "build_from_textbook",
        "description": "没课件/真题时，联网搜索教材大纲，基于公共知识为科目建立知识块库（排期依据）",
        "parameters": {"type": "object", "properties": {
            "subject": {"type": "string", "description": "科目名，如 高等数学"},
            "textbook": {"type": "string", "description": "教材名（如 张宇18讲/同济高数），可选，留空自动搜公共大纲"},
            "exam_date": {"type": "string", "description": "考试日期，可选（已设置则忽略）"}},
            "required": ["subject"]}}},
]

FUNC_MAP = {
    "parse_study_material": lambda **kw: _toolkit.parse_study_material(
        kw["subject"], kw["exam_date"], kw["courseware_path"], kw.get("paper_path", "")),
    "get_today_tasks": lambda **kw: _toolkit.get_today_tasks(),
    "get_study_overview": lambda **kw: _toolkit.get_study_overview(),
    "generate_study_plan": lambda **kw: _toolkit.generate_study_plan(
        kw.get("daily_minutes"), kw.get("reason", "用户请求"), kw.get("subject", "")),
    "generate_quiz": lambda **kw: _toolkit.generate_quiz(int(kw["block_id"]), kw.get("kind", "随堂")),
    "record_quiz_score": lambda **kw: _toolkit.record_quiz_score(int(kw["block_id"]), float(kw["score_rate"])),
    "save_mistake": lambda **kw: _db_save_mistake(
        kw.get("block_id", 0), kw["question"], kw.get("user_answer", ""),
        kw["correct_answer"], kw.get("explanation", ""), kw.get("subject", "")),
    "get_mistakes": lambda **kw: _toolkit.get_mistakes(kw.get("block_id")),
    "parse_exam_paper": lambda **kw: _toolkit.parse_exam_paper(
        kw.get("subject", ""), kw.get("paper_path", ""), kw.get("year", "")),
    "get_plan_by_subject": lambda **kw: _toolkit.get_plan_by_subject(
        kw.get("subject", "全部")),
    "generate_learning_path": lambda **kw: _toolkit.generate_learning_path(
        kw.get("subject", "全部")),
    "set_exam_date": lambda **kw: _toolkit.set_exam_date(
        kw.get("subject", ""), kw.get("date_text", "")),
    "build_from_textbook": lambda **kw: _toolkit.build_from_textbook(
        kw.get("subject", ""), kw.get("textbook", ""), kw.get("exam_date", "")),
}

# ---------- 会话上下文管理（缓存所有用户消息，最多50条） ----------
_session_questions = {}  # session_id -> [{"content": str, "timestamp": float}, ...]
_session_answers = {}    # session_id -> [{"content": str, "timestamp": float}, ...]
_MAX_QUESTIONS = 50

def _get_session_questions(session_id: str):
    return _session_questions.get(session_id, [])

def _add_question(session_id: str, content: str):
    if session_id not in _session_questions:
        _session_questions[session_id] = []
    _session_questions[session_id].append({
        "content": content,
        "timestamp": datetime.now().timestamp()
    })
    if len(_session_questions[session_id]) > _MAX_QUESTIONS:
        _session_questions[session_id] = _session_questions[session_id][-_MAX_QUESTIONS:]
    # 持久化：把用户"问的题"落库（过滤指令/确认/日期类消息，只记真正的题目），
    # 这样后端重启后"保存这道题"仍能识别最近问过的题。
    try:
        if session_id and _looks_like_a_question(content):
            import study_db
            study_db.record_asked_question(session_id, content.strip())
    except Exception:
        pass

def _add_answer(session_id: str, content: str):
    """记录一次 AI 回复（供后续对话拼接上下文，理解"9月1号是对哪天考的回答"）。"""
    if not content:
        return
    if session_id not in _session_answers:
        _session_answers[session_id] = []
    _session_answers[session_id].append({
        "content": content[:2000],
        "timestamp": datetime.now().timestamp()
    })
    if len(_session_answers[session_id]) > _MAX_QUESTIONS:
        _session_answers[session_id] = _session_answers[session_id][-_MAX_QUESTIONS:]

def _get_session_turns(session_id: str, max_turns: int = 6):
    """按时间交错拼接用户消息与 AI 回复，得到最近的对话轮次（供 LLM 理解上下文）。
    返回 [{"role": "user"/"assistant", "content": str}, ...]"""
    qs = _session_questions.get(session_id, [])
    ans = _session_answers.get(session_id, [])
    turns = []
    i = j = 0
    while i < len(qs) or j < len(ans):
        if i < len(qs):
            turns.append({"role": "user", "content": qs[i]["content"][:2000]})
            i += 1
        if j < len(ans):
            turns.append({"role": "assistant", "content": ans[j]["content"][:2000]})
            j += 1
    return turns[-max_turns * 2:]  # 最近 max_turns 轮（每轮 user+assistant）

def _clear_session_questions(session_id: str):
    if session_id in _session_questions:
        del _session_questions[session_id]
    if session_id in _session_answers:
        del _session_answers[session_id]

# ---------- 待确认上下文管理 ----------
_pending_context = {}  # session_id -> {"type": "confirmation", "action": str, "data": dict, "expires": float}

def set_pending_context(session_id: str, action: str, data: dict = None, timeout_seconds: int = 120):
    _pending_context[session_id] = {
        "action": action,
        "data": data or {},
        "expires": datetime.now().timestamp() + timeout_seconds
    }

def get_pending_context(session_id: str):
    ctx = _pending_context.get(session_id)
    if ctx and ctx.get("expires", 0) > datetime.now().timestamp():
        return ctx
    if session_id in _pending_context:
        del _pending_context[session_id]
    return None

def clear_pending_context(session_id: str):
    if session_id in _pending_context:
        del _pending_context[session_id]

# ---------- 待选择状态管理（多题选择） ----------
_pending_selection = {}  # session_id -> {"questions": [], "expires": float}

# ---------- 重做错题状态管理 ----------
_pending_review = {}  # session_id -> {"items": [错题dict...], "index": int}

# ---------- 最近保存的错题（供“这道是XX的”纠正科目） ----------
_last_saved_mistake = {}  # session_id -> mistake_id

# ---------- 大模型题目提取函数 ----------
def _extract_questions_by_llm(session_id: str, cfg: dict) -> list:
    """
    从最近50条用户消息中，调用大模型提取所有题目。
    返回题目列表（按时间从新到旧排序）。
    如果无题目，返回空列表。
    """
    questions = _get_session_questions(session_id)
    if not questions:
        return []
    
    # 只取最近50条的内容
    msgs = [q["content"] for q in questions[-50:]]
    if not msgs:
        return []
    
    # 构建prompt（注意用英文引号包裹指令词，避免 LLM 对引号风格敏感导致误判）
    prompt = f"""你是题目识别助手。从以下用户消息中，识别出所有"题目"（数学题、计算题、证明题、物理题等）。
规则：
1. 若某条消息只是操作指令（含"保存""存""第X道""全部""是""否""好""要"等）或短确认词，不要当作题目；
2. 但"保存这道题""保存最后一道"这类指令本身不是题目，它所指的那条题目消息仍要正常提取；
3. 只提取用户真正询问的题目内容本身（如"求极限 lim(x->0) sinx/x 为什么等于1？"提取为"求极限 lim(x->0) sinx/x 为什么等于1？"），忽略解释性文字。

用户消息列表（按时间从旧到新）：
{chr(10).join([f"{i+1}. {m}" for i, m in enumerate(msgs)])}

输出格式：只返回一个JSON数组，每个元素是一个字符串，代表一道题目。如果没有题目，返回空数组 []。
不要包含任何其他文字。
"""
    try:
        url = cfg["base_url"].rstrip("/")
        if not url.endswith("/chat/completions"):
            url += "/chat/completions"
        resp = requests.post(
            url,
            headers={"Authorization": f"Bearer {cfg['api_key']}", "Content-Type": "application/json"},
            json={
                "model": cfg["model"],
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.0,
                "max_tokens": 500
            },
            timeout=15
        )
        resp.raise_for_status()
        result = resp.json()["choices"][0]["message"]["content"].strip()
        # 尝试解析JSON
        import json as jsonlib
        # 去除可能的markdown代码块
        if result.startswith("```json"):
            result = result.split("```json")[1].split("```")[0].strip()
        elif result.startswith("```"):
            result = result.split("```")[1].split("```")[0].strip()
        questions_list = jsonlib.loads(result)
        if isinstance(questions_list, list):
            # 过滤空字符串
            return [q.strip() for q in questions_list if q.strip()]
        else:
            return []
    except Exception as e:
        logging.error(f"LLM提取题目失败: {e}")
        raise RuntimeError(f"题目提取服务异常: {e}")

# ---------- 辅助函数 ----------
def _looks_like_date_answer(text: str) -> bool:
    """日期/时间类答案：'9月1号' '9月1日' '9/1' '2026年9月1日' '明天' '下周三' '12月25日'
    '9.1考' '下午考' '后天'。
    通常是回答"哪天考/什么时候"等上下文问题。"""
    t = text.strip()
    if not t:
        return False
    if re.search(r"\d+\s*[月号日/.\-]", t) or re.search(r"[月号日]", t):
        # 含月/号/日的日期表达
        return len(t) <= 15
    if any(k in t for k in ["明天", "后天", "下周", "下个", "这周", "本周", "今天",
                            "下个月", "这学期", "期末", "下午", "上午", "晚上", "中午"]):
        return len(t) <= 12
    return False


def _is_asking_exam_date(session_id: str) -> bool:
    """检测：上一轮 AI 回复是否在问考试日期（结合 _session_answers 缓存）。
    命中 → 说明用户下一条很可能是日期答案，应进入"等日期"状态。"""
    ans = _session_answers.get(session_id, [])
    if not ans:
        return False
    last = ans[-1]["content"]
    return any(k in last for k in ["哪天考", "考试日期", "几号考", "什么时候考",
                                   "日期是", "哪天", "考试时间", "考高数？", "告诉我日期"])


def _reply_asks_exam_date(reply: str) -> bool:
    """判断一段 AI 回复文本是否在向用户问考试日期（直接基于 reply 文本，不依赖缓存时序）。"""
    if not reply:
        return False
    return any(k in reply for k in ["哪天考", "考试日期", "几号考", "什么时候考",
                                    "日期是", "哪天", "考试时间", "告诉我日期",
                                    "考高数", "几号", "什么时间考"])


def _find_subject_in_context(session_id: str) -> str:
    """从最近对话轮次中找出提到的科目名（支持别名，如"高数"→高等数学）。"""
    subs = _get_subject_names()
    if not subs:
        return ""
    # 从最近的 user/assistant 消息里找
    qs = _session_questions.get(session_id, [])
    ans = _session_answers.get(session_id, [])
    texts = [t["content"] for t in qs[-4:]] + [t["content"] for t in ans[-4:]]
    for text in reversed(texts):
        m = _match_subject(text, subs)
        if m:
            return m
    return ""


def _last_reply_question_type(session_id: str) -> str:
    """分析上一轮 AI 回复：它是问句吗？问的是什么？
    返回：'date'(问日期) / 'subject'(问科目) / 'confirm'(问确认要/不要/好吗)
          'number'(问编号/第几道) / 'knowledge'(讲解后问懂了没/追问语境)
          'none'(非问句，陈述/讲解)
    这是"待机理解"的核心：系统记得自己上一轮抛出的问题，用户下一条按回答接住。"""
    ans = _session_answers.get(session_id, [])
    if not ans:
        return "none"
    last = ans[-1]["content"]
    has_q = bool(re.search(r"[?？]", last)) or any(
        k in last for k in ["哪", "什么", "是否", "可以吗", "好吗", "要吗", "对不对",
                            "懂了吗", "明白吗", "同意吗", "要不要", "几点", "几号", "哪天"])
    if not has_q:
        return "none"
    if any(k in last for k in ["哪天", "几号", "什么日期", "考试日期", "什么时候考", "日期"]):
        return "date"
    if any(k in last for k in ["哪个科目", "哪科", "哪个", "哪门"]):
        return "subject"
    if any(k in last for k in ["哪本教材", "什么教材", "用哪本", "教材名", "用什么教材", "教材是"]):
        return "textbook"
    if any(k in last for k in ["第几道", "第几题", "哪道", "哪题", "编号"]):
        return "number"
    if any(k in last for k in ["懂了吗", "明白吗", "理解吗", "听懂", "清楚吗", "有疑问", "还有什么"]):
        return "knowledge"
    if any(k in last for k in ["要吗", "要不要", "好吗", "可以吗", "行吗", "是否", "对不对",
                               "同意吗", "保存吗", "存吗", "重做吗", "开始吗", "继续吗"]):
        return "confirm"
    return "other_question"


def _looks_like_short_answer(text: str) -> bool:
    """极短答案（≤6字且不是完整句子）→ 放行进快通道。
    覆盖：'9月1号'(已由日期规则) '42' '选B' 'B' '对' '可以' '是的' '没问题' '随便' 等回答。
    注意：≤6 字且不含深度词的消息几乎不可能是深度研究任务。"""
    t = text.strip()
    if not t:
        return False
    if re.search(r"证明|求证|试证|推导|计算|求导|求极限|求积分|什么是|解释|完整模式|深度研究", t):
        return False
    if len(t) <= 6:
        # 排除明显的题目/提问（含问号或超过纯短答案特征）
        if t.endswith(("？", "?", "吗")):
            return False
        return True
    return False


# 难题深度信号词：命中 → 该题应走【完整引擎】做详细解答（含步骤/推导/来源），而非快通道秒回
# 设计原则：不靠"长度"判难题（长不一定难，短也可能难）——
# 以"用户明确要求深度产出"为核心信号（要推导/证明/查资料/过程/来源等），
# 长度仅对"带多个条件/小问的复合题"作辅助（且须同时含题目动词，由调用方保证）。
_HARD_PROBLEM_HINTS = [
    # 明确要求过程/推导
    "详细推导", "详细过程", "完整推导", "完整过程", "详细步骤", "一步步",
    "要过程", "要步骤", "写出过程", "步骤详解", "推导过程", "求解过程",
    # 明确要求证明
    "证明一下", "如何证明", "详细证明", "完整证明", "证明过程", "为什么成立",
    # 明确要求查资料/来源
    "查一下", "查资料", "查文献", "查来源", "搜一下", "引用来源", "出处", "参考一下",
    # 明确要求深入/讲透
    "深入分析", "深入研究", "讲透", "讲透彻", "彻底讲清", "详细讲解", "深度讲解",
    # 明确要求多方法/全面
    "多方法", "多种解法", "多种方法", "举一反三", "全面分析", "系统讲解",
]
# 复合题辅助阈值：仅当同时命中题目动词(调用方已保证)且超过此长度才可能算难题——
# 且必须至少再含一个"小问/条件"结构特征，否则长口水话不算
_HARD_PROBLEM_LEN = 60
_COMPOUND_HINTS = ["（", "(", "①", "②", "③", "求", "证明", "计算", "分别", "其中", "若", "已知", "设"]


def _looks_like_hard_problem(text: str) -> bool:
    """判断是否"难题"（应走完整引擎深度解答）。
    核心：用户明确要求深度产出（推导/证明/查资料/过程/来源/讲透/多方法）→ 必算难题；
    辅助：题目很长(≥60字) 且 含复合结构特征（括号/小问/条件）→ 算难题。
    注意：仅由调用方在命中题目动词/答疑词后调用，避免误伤指令类消息。"""
    t = (text or "").strip()
    if not t:
        return False
    # ① 深度信号词命中 → 难题（不论长短）
    if any(k in t for k in _HARD_PROBLEM_HINTS):
        return True
    # ①b 证明/说明类 → 默认难题（证明题需要完整推导）：
    #    除非是简单恒等式证明（如"证明 (a+b)²=a²+2ab+b²"）——那种快通道即可
    if ("证明" in t or "试证明" in t or "求证" in t or "说明理由" in t or "为什么成立" in t):
        simple_identity = ("=" in t and len(t) <= 30
                           and not any(k in t for k in ["存在", "若", "区间", "定理", "连续",
                                                        "收敛", "一致", "使得", "求证", "试证明",
                                                        "为什么", "过程", "推导", "理由"]))
        if not simple_identity:
            return True
    # ② 复合题辅助：很长 + 含结构特征 → 难题
    if len(t) >= _HARD_PROBLEM_LEN:
        struct_hits = sum(1 for k in _COMPOUND_HINTS if k in t)
        if struct_hits >= 2:
            return True
    return False


_ENGINE_ROUTE_MIN_CONFIDENCE = 0.65


def _route_context(session_id: str | None) -> str:
    """Return a compact recent conversation for the engine router."""
    if not session_id:
        return "（无历史）"
    turns = []
    for role, bucket in (("学生", _session_questions), ("助手", _session_answers)):
        for item in bucket.get(session_id, [])[-4:]:
            turns.append((float(item.get("timestamp", 0)), role,
                          str(item.get("content", ""))[:240]))
    turns.sort(key=lambda item: item[0])
    return "\n".join(f"{role}: {content}" for _, role, content in turns[-6:]) or "（无历史）"


def _classify_engine_by_llm(text: str, cfg, session_id: str = None,
                            uploaded_files=None) -> str:
    """Semantically route every idle request to ``fast`` or ``deep``.

    Keywords are deliberately not used here. A low-confidence, malformed, or
    failed classification falls back to the deep engine so complex requests
    are not silently answered by the lightweight path.
    """
    t = (text or "").strip()
    if not t:
        return "deep"
    context = _route_context(session_id)
    attachment_note = f"本轮附带 {len(uploaded_files or [])} 个文件。"
    prompt = f"""你是学习系统的引擎路由器。请根据语义、最近对话和任务复杂度，决定使用快速通道还是完整引擎。不要依赖某个词是否出现，而要判断完成任务真正需要的工作量。

【快速通道 fast】
- 一次模型回答即可可靠完成的普通问答、概念解释、常规计算和简短追问；
- 查看或操作已有学习计划、错题、资料、考试日期等确定性学习功能；
- 对上一轮问题的简短承接、确认或答案。

【完整引擎 deep】
- 需要拆成多个相互依赖步骤、并行处理或汇总多项结果；
- 需要多来源检索、交叉核验、长篇报告、复杂证明或系统性分析；
- 需要综合多个上传文件，或一次模型回答难以可靠完成。

最近对话：
{context}

当前请求：{t}
{attachment_note}

只输出 JSON：{{"engine":"fast或deep","confidence":0到1之间的小数,"reason":"一句简短理由"}}"""
    try:
        url = cfg["base_url"].rstrip("/")
        if not url.endswith("/chat/completions"):
            url += "/chat/completions"
        resp = requests.post(
            url,
            headers={"Authorization": f"Bearer {cfg['api_key']}",
                     "Content-Type": "application/json"},
            json={"model": cfg["model"],
                  "messages": [{"role": "user", "content": prompt}],
                  "temperature": 0.0, "max_tokens": 120,
                  **({"thinking": {"type": "disabled"}}
                     if cfg["model"].lower().startswith("deepseek") else {})},
            timeout=15)
        resp.raise_for_status()
        raw = resp.json()["choices"][0]["message"]["content"].strip()
        raw = re.sub(r"^```json|^```|```$", "", raw, flags=re.IGNORECASE).strip()
        result = json.loads(raw)
        engine = str(result.get("engine", "")).strip().lower()
        confidence = float(result.get("confidence", 0))
        if engine not in {"fast", "deep"} or confidence < _ENGINE_ROUTE_MIN_CONFIDENCE:
            logging.warning("引擎语义分类结果无效或置信度过低，使用完整引擎")
            return "deep"
        logging.info("引擎语义分类: %s (confidence=%.2f)", engine, confidence)
        return engine
    except Exception as e:
        logging.warning(f"引擎语义分类失败，使用完整引擎: {e}")
        return "deep"


def _classify_difficulty_by_llm(text: str, cfg) -> str:
    """Backward-compatible wrapper for older callers and extensions."""
    return "simple" if _classify_engine_by_llm(text, cfg) == "fast" else "hard"


def _should_fast(query: str, session_id: str = None, uploaded_files=None) -> bool:
    """
    快速通道入口判断：活动中的本地状态机必须继续走快速通道；除此之外，
    所有请求均由大模型结合上下文做语义分流。关键词不再决定引擎。
    """
    if not query:
        return False
    if session_id and (session_id in _pending_selection or get_pending_context(session_id)
                       or session_id in _pending_review):
        return True
    try:
        import material_analyst as _ma
        cfg = _ma.sniff_llm_config()
        return _classify_engine_by_llm(
            query, cfg, session_id=session_id, uploaded_files=uploaded_files) == "fast"
    except Exception as exc:
        logging.warning("无法初始化引擎语义分类，使用完整引擎: %s", exc)
        return False


_SAVE_HINT = ["保存", "存这道", "存一下", "存起来", "我要存", "想存", "记住这道", "记录错题"]


def _looks_like_save_instruction(text: str) -> bool:
    """
    判断用户消息是否为“保存错题”相关指令（而非题目内容）。
    覆盖：确认词（要/嗯/好/行/对/是）、编号选择（1 / 第2道）、
    全量（全部/所有/都）、以及保存类关键词。
    注意：“错题本”单独出现属于查看意图，不再误判为保存。
    """
    if not text:
        return False
    t = text.strip()
    if len(t) <= 3 and any(k in t for k in ["要", "嗯", "好", "行", "对", "是", "不用", "不要"]):
        return True
    if re.fullmatch(r"第?\s*\d+\s*道?", t):
        return True
    if re.fullmatch(r"(全部|所有|全都要|都)(保存)?", t):
        return True
    return any(k in t for k in _SAVE_HINT)

# ---------- 核心处理函数 ----------
def _handle_save_question(session_id: str, messages, cfg, user_input: str = ""):
    """
    保存错题核心入口（意图状态机）：
    1. 用户说"这道/最后一道/最后一题" → 确定性取缓存里最近一条非指令消息作为题目，直接保存
       （不依赖 LLM 提取——"保存这道题"本身也是指令消息，若混入 LLM 提取列表会导致误判）
    2. 从会话缓存提取题目（LLM 兜底）
    3. 只有一道 → 走"先问答案再入库"流程
    4. 有多道 → 列出清单让用户选（编号 / 全部 / 直接发新题）
    5. 没有题目 → 引导用户手动提供
    """
    # ① 确定性路径："保存这道题/最后一道" → 直接用最近一条用户题目（跳过指令消息）
    if any(k in user_input for k in ["这道", "最后一道", "最后一题"]):
        last_q = _get_last_question_from_cache(session_id)
        if last_q:
            return _start_save_flow(session_id, last_q, messages, cfg)
        # 缓存里没有题目消息，回退 LLM 提取
        try:
            questions = _extract_questions_by_llm(session_id, cfg)
        except Exception as e:
            return (f"提取题目时出了点问题（{e}）。"
                    "你可以直接把题目发给我，或者说“记录错题：题目xxx，错误答案xxx，正确答案xxx”手动保存。")
        if questions:
            return _start_save_flow(session_id, questions[-1], messages, cfg)
        return ("我翻了咱们这段对话，没找到你说的题目。"
                "你可以把题目直接发给我，或者说“记录错题：题目xxx，错误答案xxx，正确答案xxx”手动保存。")

    # ② LLM 兜底路径（"保存错题"等模糊说法 → 列清单）
    try:
        questions = _extract_questions_by_llm(session_id, cfg)
    except Exception as e:
        return (f"提取题目时出了点问题（{e}）。"
                "你可以直接把题目发给我，或者说“记录错题：题目xxx，错误答案xxx，正确答案xxx”手动保存。")

    if not questions:
        return ("我翻了咱们这段对话，没找到你说的题目。"
                "你可以把题目直接发给我，或者说“记录错题：题目xxx，错误答案xxx，正确答案xxx”手动保存。")

    # 只有一道题：直接进入“先问答案再入库”流程
    if len(questions) == 1:
        return _start_save_flow(session_id, questions[0], messages, cfg)

    # 多道题：列出清单，保存到待选择状态（120秒有效）
    _pending_selection[session_id] = {
        "questions": questions,
        "expires": datetime.now().timestamp() + 120
    }
    lines = ["这段对话里我找到了这些题，你想保存哪一道？回复编号（如 1），或说“全部”保存所有："]
    for i, q in enumerate(questions, start=1):
        content = q[:60] + ("..." if len(q) > 60 else "")
        lines.append(f"{i}. {content}")
    lines.append("也可以直接说“保存这道题”，我会保存最后一道。")
    return "\n".join(lines)


# 指令类检测器集合（判断缓存消息是否为"操作指令"而非"题目内容"）
# 注意：相关 _looks_like_* 函数定义在文件后部，因此用函数内延迟引用，避免模块级 NameError


def _looks_like_a_question(text: str) -> bool:
    """判断一条用户消息是否"像一道题"（应被持久化记录）。
    排除：操作指令/确认词/日期答案/极短词；保留：题目类（含数学特征或长度足够）。"""
    t = (text or "").strip()
    if not t:
        return False
    checks = (_looks_like_save_instruction, _looks_like_view_instruction,
              _looks_like_review_instruction, _looks_like_master_instruction,
              _looks_like_summary_instruction, _looks_like_subject_correction,
              _looks_like_paper_parse_instruction, _looks_like_plan_view_instruction,
              _looks_like_generate_plan_instruction, _looks_like_learning_path_instruction,
              _looks_like_review_stop, _looks_like_date_answer)
    if any(check(t) for check in checks):
        return False
    if len(t) <= 6:
        # 短消息：除非明显是题目特征（数学符号/动词+题），否则不算题目（"好""2""对"等）
        return bool(re.search(r"[=＝∫∑Σ√≤≥∞xX0-9＋×÷²³]|lim|sin|cos|tan|导数|积分|方程|矩阵"
                              r"|求|证明|计算|求解|为什么|怎么|为何|题|解", t))
    # 长���息：结尾是"？/?"的疑问句也可能是题（如"为什么等于1？"）→ 保留；
    # 排除纯闲聊（你好/谢谢/再见等）
    if any(k in t for k in ["你好", "谢谢", "再见", "拜拜", "在吗", "辛苦了", "哈哈", "好的谢谢"]):
        return False
    return True


def _get_last_question_from_cache(session_id: str) -> str:
    """确定性取最近一条"像题目"的用户消息（倒序遍历，跳过指令/确认/日期类消息）。
    内存缓存没有时回退查数据库（持久化，后端重启后仍能识别）。"""
    checks = (_looks_like_save_instruction, _looks_like_view_instruction,
              _looks_like_review_instruction, _looks_like_master_instruction,
              _looks_like_summary_instruction, _looks_like_subject_correction,
              _looks_like_paper_parse_instruction, _looks_like_plan_view_instruction,
              _looks_like_generate_plan_instruction, _looks_like_learning_path_instruction,
              _looks_like_review_stop, _looks_like_date_answer)
    qs = _get_session_questions(session_id)
    for q in reversed(qs):
        content = (q.get("content") or "").strip()
        if not content:
            continue
        if any(check(content) for check in checks):
            continue
        # 太短（≤6字）且非题目特征 → 跳过（如"好""2""对"；但"x²+1""lim"这类保留）
        if len(content) <= 6 and not re.search(r"[=＝∫∑Σ√≤≥∞xX0-9＋×÷²³]|lim|sin|cos|tan|导数|积分|方程|矩阵", content):
            continue
        return content
    # 内存没有 → 回退数据库（重启后仍能识别"之前问过的题"）
    try:
        import study_db
        recent = study_db.get_recent_asked_questions(session_id, limit=10)
        for q in recent:
            content = (q or "").strip()
            if not content:
                continue
            if any(check(content) for check in checks):
                continue
            return content
    except Exception:
        pass
    return ""


# 从“题目+答案”合并文本中提取用户答案，如“求积分∫(2x+1)dx，我算成了 x²+1”
_ANSWER_PATTERN = re.compile(
    r"(?:我|自己)?\s*(?:算成|算出来|写成|答成|选(?:了|择)?|我的答案是|我的结果是|算的是|填了|写的是|我算的|算得)\s*(?:了|的)?\s*[是为：:号]?\s*(.+)$"
)


def _extract_user_answer_from_text(text: str):
    """从“题目 + 我算成了X”这类文本中提取用户的答案；提取不到返回 None。"""
    if not text:
        return None
    m = _ANSWER_PATTERN.search(text)
    if m:
        ans = m.group(1).strip().rstrip("。.!！")
        if ans:
            return ans
    return None


_ASK_ANSWER_HINTS = ["你不是", "算出来了吗", "你知道答案", "你告诉我", "帮我算", "你说吧", "你说呢", "你算算", "你直接告诉我", "你知道的"]


def _extract_correct_answer_by_llm(session_id: str, messages, cfg, question: str):
    """
    获取正确答案（v2·独立验算）：
    - 让大模型**独立重新求解**这道题（不抄对话里的解答），再做交叉验算，
      与历史解答对比，以独立验算结果为准——保证保存的答案是清扫过的、可信的。
    仍拿不到返回 None。
    """
    try:
        history_msgs = [m.get("content", "") for m in (messages or [])
                        if isinstance(m, dict) and m.get("role") != "system"]
        history_msgs = history_msgs[-10:]
        hist_text = chr(10).join(history_msgs) if history_msgs else "（无历史解答）"
        prompt = f"""你是严格的高等数学解题验算专家。学生要把下面这道题记入错题本，
请你【独立重新求解并验算】，给出可信的最终答案。

【题目】
{question}

【历史对话】（仅作参考：里面可能包含 AI 之前的解答，可能有错，禁止直接抄录）
{hist_text}

【执行步骤（务必按序完成）】
1. 独立求解：先不看历史对话，自己完整推导/计算这道题，得到答案 A；
2. 交叉验算：把答案 A 代回题目验证，或换一种方法再算一遍，确认无误；
3. 对照核查：再看历史对话里的解答，与你算出的答案 A 对比；
   若一致 → 采信；若不一致 → 以你独立验算的答案 A 为准；
4. 清扫输出：只保留最终答案本身，去掉解释、过程、单位和废话
   （如 "1/3" "x²+1" "选B" "-2" "极限不存在"）。

【输出格式】
最后一行必须严格是：ANSWER: <最终答案>
若确实无法确定答案，输出：ANSWER: NOT_FOUND
不要把推导过程写进输出，不要有多余文字。
"""
        url = cfg["base_url"].rstrip("/")
        if not url.endswith("/chat/completions"):
            url += "/chat/completions"
        resp = requests.post(
            url,
            headers={"Authorization": f"Bearer {cfg['api_key']}", "Content-Type": "application/json"},
            json={"model": cfg["model"],
                  "messages": [{"role": "user", "content": prompt}],
                  "temperature": 0.0, "max_tokens": 300},
            timeout=30
        )
        resp.raise_for_status()
        raw = resp.json()["choices"][0]["message"]["content"].strip()
        # 解析最后一行 ANSWER: xxx
        ans = ""
        for line in reversed(raw.splitlines()):
            line = line.strip()
            if line.upper().startswith("ANSWER:"):
                ans = line.split(":", 1)[1].strip()
                break
        if not ans:
            # 兜底：全文搜索 ANSWER: 标记
            import re as _re
            m = _re.search(r"ANSWER\s*[:：]\s*(.+)", raw, _re.I)
            ans = m.group(1).strip() if m else ""
        if not ans or "NOT_FOUND" in ans.upper():
            return None
        return ans[:200]
    except Exception as e:
        logging.error(f"自动提取正确答案失败: {e}")
        return None


def _do_save_mistake(session_id: str, question: str, correct_ans: str, cfg) -> str:
    """保存错题并给出反馈（自动识别科目、记录最近保存的错题 id）。"""
    subject = ""
    if cfg:
        try:
            subject = _detect_subject_by_llm(question, cfg) or ""
        except Exception:
            subject = ""
    try:
        mid = FUNC_MAP["save_mistake"](
            block_id=0,
            question=question,
            user_answer="",
            correct_answer=correct_ans,
            explanation="",
            subject=subject)
    except Exception as e:
        return f"保存失败：{e}"
    _last_saved_mistake[session_id] = mid
    subj_text = f"【{subject}】" if subject else "【未分类】"
    ans_text = f"正确答案：{correct_ans}" if correct_ans else "正确答案暂缺"
    tip = ("\n科目没识别对？说“这道是XX的”即可纠正。" if subject else
           "\n我没认出科目，说“这道是XX的”可补上。")
    return (f"✅ 已保存到{subj_text}错题本：\n题目：{question[:80]}{'...' if len(question) > 80 else ''}\n"
            f"{ans_text}{tip}")


def _start_save_flow(session_id: str, question: str, messages=None, cfg=None):
    """
    单题保存流程（v3）：先尝试自动得出正确答案（直接解题）——
    拿到了就立即保存，不再询问；只有确实拿不到才问用户。
    """
    if cfg:
        auto = _extract_correct_answer_by_llm(session_id, messages, cfg, question)
        if auto:
            return _do_save_mistake(session_id, question, auto, cfg) + "\n（正确答案自动得出，直接保存）"
    set_pending_context(session_id, "await_correct", {"question": question})
    return (f"好，我来保存这道题：\n{question}\n\n"
            "这道题的**正确答案**是什么？"
            "（不知道就回复“跳过”，或说“你不是算出来了吗”让我从解答里找）")


def _handle_pending_answer(session_id: str, user_input: str, messages=None, cfg=None):
    """
    处理保存流程的“等正确答案”环节（兜底）：用户给出正确答案 → 入库。
    若用户表示“跳过/你不是算出来了吗”，尝试自动提取/直接解题。
    返回回复文本；如果当前不存在待确认状态，返回 None（交给后续逻辑）。
    """
    ctx = get_pending_context(session_id)
    if not ctx or ctx.get("action") != "await_correct":
        return None
    data = ctx["data"]
    question = data["question"]
    skip = user_input.strip() in ("跳过", "不写", "没有", "无")
    want_auto = skip or any(k in user_input for k in _ASK_ANSWER_HINTS)

    correct_ans = ""
    if want_auto and cfg:
        auto = _extract_correct_answer_by_llm(session_id, messages, cfg, question)
        if auto:
            correct_ans = auto
    if not correct_ans and not skip:
        # 剥离“正确答案是/答案:”等前缀，尽量存纯答案
        correct_ans = re.sub(r"^(正确答案|标准答案|答案)[是为：:]\s*", "", user_input.strip())

    clear_pending_context(session_id)
    return _do_save_mistake(session_id, question, correct_ans, cfg)


def _detect_subject_by_llm(question: str, cfg) -> str:
    """让 LLM 判断题目属于哪个学科；失败返回空串。"""
    try:
        url = cfg["base_url"].rstrip("/")
        if not url.endswith("/chat/completions"):
            url += "/chat/completions"
        prompt = (f"判断下面这道题属于哪个学科，只输出学科名（如：高等数学/线性代数/离散数学/"
                  f"概率论/大学物理/英语/计算机/数据结构/其他），不要任何解释。\n题目：{question[:300]}")
        resp = requests.post(
            url,
            headers={"Authorization": f"Bearer {cfg['api_key']}", "Content-Type": "application/json"},
            json={"model": cfg["model"],
                  "messages": [{"role": "user", "content": prompt}],
                  "temperature": 0.0, "max_tokens": 20},
            timeout=15
        )
        resp.raise_for_status()
        ans = resp.json()["choices"][0]["message"]["content"].strip()
        ans = re.sub(r"[。.！!\s]", "", ans)
        return ans[:12]
    except Exception as e:
        logging.error(f"科目识别失败: {e}")
        return ""


def _handle_selection(session_id: str, user_input: str, cfg=None):
    """处理多题选择回复：编号 → 单题保存流程；全部/所有 → 批量保存；直接发新题 → 手动保存。"""
    pending = _pending_selection.get(session_id)
    if not pending:
        return None
    # 检查是否超时
    if datetime.now().timestamp() > pending["expires"]:
        _pending_selection.pop(session_id, None)
        return "选择已超时，请重新说“保存错题”。"

    questions = pending["questions"]

    # “保存这道题/最后一道”→ 保存列表中的最后一道（你刚问的那道）
    if any(k in user_input for k in ["这道", "最后一道", "最后一题"]):
        _pending_selection.pop(session_id, None)
        return _start_save_flow(session_id, questions[-1], None, cfg)

    # 编号选择：第2道 / 2
    match = re.search(r"第?\s*(\d+)\s*道?", user_input)
    if match:
        idx = int(match.group(1)) - 1
        if 0 <= idx < len(questions):
            _pending_selection.pop(session_id, None)
            return _start_save_flow(session_id, questions[idx], None, cfg)
        return f"编号无效，请输入 1 到 {len(questions)} 之间的数字。"

    # 全部 / 所有 / 都
    if any(k in user_input for k in ["全部", "所有", "都"]):
        _pending_selection.pop(session_id, None)
        saved = []
        failed = []
        for q in questions:
            try:
                FUNC_MAP["save_mistake"](
                    block_id=0,
                    question=q,
                    user_answer="",
                    correct_answer="",
                    explanation=""
                )
                saved.append(q)
            except Exception as e:
                failed.append((q, str(e)))
        msg = f"✅ 已保存全部 {len(saved)} 道题"
        if saved:
            msg += "：\n" + "\n".join(
                f"{i}. {s[:50]}{'...' if len(s) > 50 else ''}" for i, s in enumerate(saved, 1))
        if failed:
            msg += f"\n❌ 有 {len(failed)} 道题保存失败：\n" + "\n".join(
                f"- {f[0][:30]}…（原因：{f[1]}）" for f in failed)
        msg += "\n提示：这些题暂未填正确答案，可逐道说“补第X道正确答案”补上。"
        return msg

    # 用户直接发的新题目（长度>5），视为手动提供题目
    if len(user_input.strip()) > 5:
        _pending_selection.pop(session_id, None)
        return _start_save_flow(session_id, user_input.strip(), None, cfg)

    return "我没理解你的选择。请回复编号（如 1）、说“全部”，或者直接发题目内容让我保存。"


# ---------- 查看 / 重做 / 总结 / 科目纠正 ----------
_SUBJECT_ALIAS = {
    "高数": "高等数学", "线代": "线性代数", "离散": "离散数学",
    "概率": "概率论", "大物": "大学物理", "物理": "大学物理",
    "数据结构": "数据结构", "计组": "计算机组成原理",
}


def _get_subject_names():
    """返回数据库里所有科目名（用于计划查看意图匹配）。"""
    try:
        import study_db
        return [s["name"] for s in study_db.get_subject_overview()]
    except Exception:
        return []


def _match_subject(user_input: str, candidates) -> str | None:
    """从用户输入中匹配科目名（支持别名），匹配不到返回 None"""
    for c in candidates:
        if c and c != "未分类" and c in user_input:
            return c
    for alias, full in _SUBJECT_ALIAS.items():
        if alias in user_input and full in candidates:
            return full
    return None


def _subject_for_plan_request(text: str) -> str | None:
    """从计划请求中解析课程名，兼容已建科、常见别名与任意新课程。"""
    known = _match_subject(text, _get_subject_names())
    if known:
        return known
    # 新科目尚未入库时也要能走“设置考试日期 → 联网建库”的流程。
    aliases = {
        "高等数学": ("高等数学", "高数", "微积分"),
        "线性代数": ("线性代数", "线代"),
        "离散数学": ("离散数学", "离散"),
        "概率论": ("概率论", "概率统计", "数理统计"),
        "大学英语": ("大学英语", "英语", "四级", "六级"),
        "程序设计": ("程序设计", "Python", "Java", "C++", "数据结构"),
    }
    normalized = (text or "").lower()
    for subject, names in aliases.items():
        if any(name.lower() in normalized for name in names):
            return subject
    # 不把课程名限制在少数预置别名。诸如“有机化学，12月20日考，
    # 帮我制定复习计划”应直接进入建课流程，而不是再次问“是哪一科”。
    import re as _re
    before_plan = _re.split(r"(?:期末)?(?:复习|学习|备考)计划", text, maxsplit=1)[0]
    if before_plan != text:
        for segment in _re.split(r"[，,。；;：:]", before_plan):
            candidate = _re.sub(r"^(?:请|帮我|给我|麻烦|想|我要|我想|能否|可以)?"
                                r"(?:制定|生成|安排|做|排|弄|搞)?(?:一[份个])?", "", segment.strip())
            candidate = candidate.strip(" 《》“”'\"的 ")
            if (2 <= len(candidate) <= 24 and
                    not _re.search(r"\d|月|日|考试|什么时候", candidate)):
                return candidate
    return None


def _looks_like_view_instruction(text: str) -> bool:
    """查看错题意图：覆盖「查看错题/错题本/我的错题/错题/有哪些错题/看看/调出来/看下」等说法。
    注意：保存/重做/总结指令在调用顺序上先于本判断，不会冲突。"""
    if "错题" not in text:
        return False
    if any(k in text for k in ["查看", "看看", "看下", "看一下", "查", "显示", "有哪些",
                               "给我", "调出来", "看看", "我的错题", "错题本", "整一下",
                               "整下", "处理下", "太多了", "好多", "怎么办", "呢"]):
        return True
    return text.strip() in ("错题", "错题本", "我的错题")


def _looks_like_save_instruction(text: str) -> bool:
    """
    判断用户消息是否为“保存错题”相关指令（而非题目内容）。
    覆盖：确认词（要/嗯/好/行/对/是）、编号选择（1 / 第2道）、
    全量（全部/所有/都）、以及保存类关键词。
    注意：“错题本”单独出现属于查看意图，不再误判为保存。
    """
    if not text:
        return False
    t = text.strip()
    # 口语化保存：'帮我记一下/记下来/存下/记住' + 指代（这道/刚才/那个/这个）
    if any(k in t for k in ["记一下", "记下来", "记上", "记住", "存下", "存了", "保存",
                            "存这道", "存一下", "存起来", "我要存", "想存"]):
        return True
    if len(t) <= 3 and any(k in t for k in ["要", "嗯", "好", "行", "对", "是", "不用", "不要"]):
        return True
    if re.fullmatch(r"第?\s*\d+\s*道?", t):
        return True
    if re.fullmatch(r"(全部|所有|全都要|都)(保存)?", t):
        return True
    if re.fullmatch(r"\d+\s*道题\s*(都|全)?(存|保存|记)", t):
        return True
    return any(k in t for k in _SAVE_HINT)


def _looks_like_review_instruction(text: str) -> bool:
    return any(k in text for k in ["重做", "刷错题", "复习错题", "再来一遍", "重刷"])


def _looks_like_master_instruction(text: str) -> bool:
    """标记掌握意图：'第2道已掌握' / '标记第2、4道' / '第2道我会了' / '掌握第2道' / '全部掌握'。
    注意：单纯说'掌握了'不带编号/全部时不命中（可能是闲聊），交给 LLM。"""
    t = text.strip()
    if "全部掌握" in t or "全部标记" in t or "都掌握了" in t:
        return True
    if not re.search(r"\d", t):  # 必须带题号
        return False
    return any(k in t for k in ["已掌握", "掌握了", "我会了", "会了", "标记掌握",
                                "掌握第", "标记第", "不用重做", "不用再练", "过了这道"])


def _looks_like_review_stop(text: str) -> bool:
    """重做过程中想中断：'停下/不做了/先这样/退出重做/结束'。"""
    t = text.strip()
    return any(k in t for k in ["停止", "停下", "不做了", "先这样", "退出重做",
                                "结束重做", "不重做了", "到此为止", "算了"])


def _looks_like_summary_instruction(text: str) -> bool:
    return any(k in text for k in ["整理错题", "错题总结", "知识点总结", "薄弱点", "总结错题"])


def _looks_like_subject_correction(text: str) -> bool:
    return (len(text) <= 25 and bool(re.search(
        r"(?:是|属于|归到|放到|记到)\s*[\u4e00-\u9fa5A-Za-z]{2,12}\s*的", text)))


def _looks_like_paper_parse_instruction(text: str) -> bool:
    """真题解析意图：解析/分析/导入/识别 + 真题。如「解析真题 2024高数.pdf」。"""
    if "真题" not in text:
        return False
    return any(k in text for k in ["解析", "分析", "导入", "识别", "提取", "录入", "上传"])


def _looks_like_plan_view_instruction(text: str) -> bool:
    """查看计划意图：查看/看看/显示/给我 + 计划，或「XX计划」（支持别名，如"高数计划"）。
    与 generate_study_plan（生成/重排）区分：带 生成/重排/制定/更新 关键词时归生成意图。"""
    if "计划" not in text:
        return False
    if any(k in text for k in ["生成", "重排", "制定", "更新", "重新", "怎么排", "帮我排"]):
        return False
    if any(k in text for k in ["查看", "看看", "显示", "给我", "我的计划", "当前", "最新", "看一下", "看下", "目前"]):
        return True
    # "高数计划"/"高数的基础计划"/"数学计划" 这类点名科目（支持别名匹配）
    subs = _get_subject_names()
    return bool(subs) and bool(_match_subject(text, subs)) and "计划" in text


def _looks_like_generate_plan_instruction(text: str) -> bool:
    """生成/重排计划意图：'生成复习计划' / '帮我排个计划' / '重排计划' / '搞个计划'。
    这是高频操作，确定性路由到 generate_study_plan（不依赖 LLM 恰好调用工具）。"""
    if "计划" not in text:
        return False
    return any(k in text for k in ["生成", "重排", "制定", "更新", "重新", "怎么排", "帮我排",
                                   "排个计划", "排期", "安排计划", "搞个计划", "搞计划",
                                   "弄个计划", "做个计划"])


def _looks_like_learning_path_instruction(text: str) -> bool:
    """学习路径意图：怎么学/学习路径/学习路线。
    排除：图论术语"最短/最长路径"、"这道题/这题/怎么解/怎么做"（那是答疑）。"""
    if "最短路径" in text or "最长路径" in text:
        return False
    # "这道题怎么学/这题怎么解" → 答疑，不是学习路径
    if any(k in text for k in ["这道题", "这题", "这道", "题目", "怎么解", "怎么做"]):
        return False
    return any(k in text for k in ["学习路径", "学习路线", "路径", "怎么学", "怎么复习", "怎么安排"])


def _handle_view_mistakes(session_id: str, user_input: str):
    """查看错题：无科目 → 汇总概览（并进入"待选科"状态，下一条回科目名即展开该科）；带科目 → 该科列表。"""
    try:
        from study_db import get_mistake_overview, get_mistakes_by_subject
    except Exception as e:
        return f"查询错题失败：{e}"
    overview = get_mistake_overview()
    if not overview:
        return "📭 错题本还是空的，保存几道错题后再来看吧。"
    subj = _match_subject(user_input, [o["subj"] for o in overview])
    if subj:
        rows = get_mistakes_by_subject(subj)
        lines = [f"📚 【{subj}】错题（{len(rows)} 道）："]
        for i, r in enumerate(rows, 1):
            mark = "✅已攻克" if r["mastered"] else "⬜未攻克"
            lines.append(f"{i}. {r['question'][:46]}{'…' if len(r['question']) > 46 else ''} "
                         f"[{mark}] 重做{r['review_count']}次")
        lines.append("说“重做第X道”可重做，说“重做”刷全部未攻克。")
        return "\n".join(lines)
    # 概览 + 进入"待选科"状态：下一条直接回科目名即可展开
    set_pending_context(session_id, "await_subject",
                        {"action": "view", "subjects": [o["subj"] for o in overview]})
    lines = ["📚 错题本概览："]
    for o in overview:
        lines.append(f"  【{o['subj']}】{o['total']} 道（已攻克 {o['mastered_cnt']}）")
    lines.append("直接回复科目名（如：高等数学）查看该科错题；说“重做”开始刷未攻克。")
    return "\n".join(lines)


def _handle_await_subject(session_id: str, user_input: str):
    """处理“待选科”状态的回复：回科目名 → 展开该科错题。返回 None 表示不处理（交给后续）。"""
    ctx = get_pending_context(session_id)
    if not ctx or ctx.get("action") != "await_subject":
        return None
    try:
        from study_db import get_mistakes_by_subject
    except Exception as e:
        return f"查询错题失败：{e}"
    candidates = ctx.get("data", {}).get("subjects", [])
    subj = _match_subject(user_input, candidates)
    if not subj:
        # 没匹配到科目：保留状态，重新提示
        return ("我不太确定你说的是哪个科目。可以回复科目名，例如："
                + "、".join(candidates[:5]))
    clear_pending_context(session_id)
    rows = get_mistakes_by_subject(subj)
    lines = [f"📚 【{subj}】错题（{len(rows)} 道）："]
    for i, r in enumerate(rows, 1):
        mark = "✅已攻克" if r["mastered"] else "⬜未攻克"
        lines.append(f"{i}. {r['question'][:46]}{'…' if len(r['question']) > 46 else ''} "
                     f"[{mark}] 重做{r['review_count']}次")
    lines.append("说“重做第X道”可重做，说“重做”刷全部未攻克。")
    return "\n".join(lines)


def _handle_await_exam_date(session_id: str, user_input: str):
    """处理"等考试日期"状态的回复：无论用户回什么日期格式，
    本地解析并写入数据库（确定性逻辑，不依赖 LLM）。
    返回 None 表示不处理（当前不在该状态）。"""
    ctx = get_pending_context(session_id)
    if not ctx or ctx.get("action") != "await_exam_date":
        return None
    subject = ctx.get("data", {}).get("subject", "")
    if not subject:
        clear_pending_context(session_id)
        return None
    # 本地解析日期（复用 toolkit 的中文日期解析逻辑）
    import re as _re
    from datetime import date as _date
    t = user_input.strip()
    year = _date.today().year
    m = _re.search(r"(20\d{2})[年/\-.]\s*(\d{1,2})[月/\-.]\s*(\d{1,2})", t)
    if m:
        year, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
    else:
        m = _re.search(r"(\d{1,2})\s*[月/]\s*(\d{1,2})\s*[号日]?", t)
        if m:
            mo, d = int(m.group(1)), int(m.group(2))
        else:
            return ("我记一下：请告诉我《%s》考试日期，比如「9月1号」或「2026-09-01」。"
                    % subject)
    try:
        exam = _date(year, mo, d)
        if exam < _date.today():
            exam = _date(year + 1, mo, d)
    except ValueError:
        return "这个日期好像不对（比如月份超了12），再给我一个有效日期？"
    clear_pending_context(session_id)
    try:
        import study_db
        sid, created = study_db.set_subject_exam_date(subject, exam.isoformat())
    except Exception as e:
        return f"保存考试日期失败：{e}"
    action = "已创建科目并设置" if created else "已更新"
    return (f"✅ 《{subject}》考试日期{action}为 {exam.isoformat()}。"
            "现在可以说「生成复习计划」来排期，或上传课件/真题丰富计划内容。")


def _parse_question_numbers(text: str):
    """从'重做第2、4道'/'2,4'/'第2道 第4道'中提取题号列表 → [2,4]"""
    return [int(n) for n in re.findall(r"\d+", text)]


def _handle_review(session_id: str, user_input: str, cfg=None):
    """开始重做：取未攻克错题（可按科目），列出编号清单。
    不强制进入逐题——由用户决定"重做第X道"还是"第X道已掌握"。"""
    try:
        from study_db import get_mistake_overview, get_mistakes_by_subject
    except Exception as e:
        return f"查询错题失败：{e}"
    overview = get_mistake_overview()
    subj = _match_subject(user_input, [o["subj"] for o in overview]) if overview else None
    rows = get_mistakes_by_subject(subj, only_unmastered=True)
    if not rows:
        if not get_mistakes_by_subject(subj):
            return "错题本是空的，先去保存几道错题吧。"
        return "太棒了，这个科目没有未攻克的错题了！"
    # 存为待重做清单（编号=列表顺序）
    _pending_review[session_id] = {"items": rows, "index": -1}
    lines = [f"📋 未攻克错题（{len(rows)} 道，{'全部科目' if not subj else subj}）："]
    for i, r in enumerate(rows, 1):
        lines.append(f"  {i}. {r['question'][:40]}{'…' if len(r['question']) > 40 else ''}"
                     f"（{r['subject'] or '未分类'}）")
    lines.append("\n回复「重做第X道」开始做（可多道，如：重做第2、4道）；"
                 "「第X道已掌握」直接标记掌握；也可以直接说其他指令，随时打断。")
    return "\n".join(lines)


def _render_review_question(session_id: str):
    p = _pending_review.get(session_id)
    if not p or p["index"] < 0:
        return None
    item = p["items"][p["index"]]
    return (f"第 {p['index'] + 1}/{len(p['items'])} 道（{item['subject'] or '未分类'}）：\n"
            f"{item['question']}\n\n直接回复你的答案即可（答了就过，不用解释）。"
            "也可以说「跳过」「停止」或任何指令打断。")


def _handle_review_select(session_id: str, user_input: str):
    """重做第X道（可多道）或"start"（从头开始）→ 进入逐题作答。"""
    p = _pending_review.get(session_id)
    if not p:
        return None
    t = user_input.strip()
    if t == "start" or any(k in t for k in ["我要重做", "开始", "开始做", "从头", "开做", "继续"]):
        # 从头开始：取全部清单
        items = list(p["items"])
        if not items:
            return None
        p["items"] = items
        p["index"] = 0
        return _render_review_question(session_id)
    nums = [n for n in _parse_question_numbers(user_input) if 1 <= n <= len(p["items"])]
    if not nums:
        return None
    # 保持清单顺序，去重
    items = []
    seen = set()
    for n in nums:
        if n not in seen:
            seen.add(n)
            items.append(p["items"][n - 1])
    p["items"] = items
    p["index"] = 0
    return _render_review_question(session_id)


def _judge_answer(item: dict, user_input: str) -> bool:
    """客观题判分：忽略空白/大小写的包含比较；答案为空的视为未答。"""
    correct = (item.get("correct_answer") or "").strip().lower()
    u = user_input.strip().lower()
    if not correct or not u:
        return False
    if u.replace(" ", "") == correct.replace(" ", ""):
        return True
    return correct in u or u in correct


def _handle_review_answer(session_id: str, user_input: str, cfg=None):
    """处理重做作答：答了就过——记录重做次数，轻判分给一句反馈，
    然后自动进入下一题（不强制循环，答错也不卡住）。"""
    p = _pending_review.get(session_id)
    if not p or p["index"] < 0:
        return None
    item = p["items"][p["index"]]
    # 跳过本题
    if any(k in user_input for k in ["跳过", "下一题", "下一道", "换一题", "先跳过"]):
        p["index"] += 1
        if p["index"] < len(p["items"]):
            return "⏭️ 已跳过。\n\n" + _render_review_question(session_id)
        _pending_review.pop(session_id, None)
        return "本轮重做结束，剩余的题随时说「重做」继续。"
    try:
        from study_db import record_mistake_review
        is_correct = _judge_answer(item, user_input)
        st = record_mistake_review(item["id"], is_correct)
    except Exception as e:
        return f"记录重做失败：{e}"
    lines = []
    if is_correct:
        lines.append("✅ 对了！")
    else:
        lines.append(f"已记录。正确答案：{item['correct_answer'] or '（暂缺）'}。")
    if st["mastered"] and not item["mastered"]:
        lines.append("🎉 已标记「已攻克」！")
    # 答了就过：自动进入下一道；没有则结束（不强制循环）
    p["index"] += 1
    if p["index"] < len(p["items"]):
        lines.append("\n" + _render_review_question(session_id))
    else:
        _pending_review.pop(session_id, None)
        lines.append("\n✅ 本轮重做完成！回复「重做」可继续，或其他指令随时打断。")
    return "\n".join(lines)


def _handle_mark_mastered(session_id: str, user_input: str):
    """标记掌握：'第2、4道已掌握' → 直接 mastered=1，不答题。"""
    try:
        from study_db import get_conn, get_mistakes_by_subject
    except Exception as e:
        return f"标记失败：{e}"
    # 确定编号 → 错题id 的映射：优先用当前重做清单，其次用全部未攻克
    p = _pending_review.get(session_id)
    if p and p["index"] >= 0:
        items = p["items"]
        rows = items
        pop_after = True
    else:
        rows = get_mistakes_by_subject(None, only_unmastered=True)
        pop_after = False
    if not rows:
        return "没有未攻克的错题可标记。"
    conn = get_conn()
    done, not_found = [], []
    # "全部掌握"（无编号）→ 标记全部未攻克
    if "全部掌握" in user_input or "全部标记" in user_input or "都掌握了" in user_input:
        for i, r in enumerate(rows, 1):
            conn.execute("UPDATE mistakes SET mastered=1 WHERE id=?", (r["id"],))
            done.append(i)
    else:
        for n in _parse_question_numbers(user_input):
            if 1 <= n <= len(rows):
                conn.execute("UPDATE mistakes SET mastered=1 WHERE id=?", (rows[n - 1]["id"],))
                done.append(n)
            else:
                not_found.append(n)
    conn.commit()
    conn.close()
    if pop_after:
        _pending_review.pop(session_id, None)
    msg = f"✅ 已把第 {', '.join(map(str, done))} 道标记为已掌握。" if done else ""
    if not_found:
        msg += f" 编号 {', '.join(map(str, not_found))} 超出范围（共{len(rows)}道）。"
    if done and len(done) == len(rows):
        msg += " 全部攻克，错题本清空啦！"
    if pop_after:
        msg += " 重做流程已退出，有其他需要直接说～"
    return msg.strip()


def _handle_subject_correction(session_id: str, user_input: str, cfg=None):
    """用户纠正科目：把最近保存的错题改到正确科目。
    优先用内存缓存的最近错题 id；跨会话/重启后回退查数据库最近一条错题。"""
    m = re.search(r"(?:是|属于|归到|放到|记到)\s*([\u4e00-\u9fa5A-Za-z]{2,12})\s*的?", user_input)
    if not m:
        return None
    subject = m.group(1).strip("的了")
    mid = _last_saved_mistake.get(session_id)
    if not mid:
        # 兜底：查该会话最近保存的错题（跨会话/重启后仍可纠正）
        try:
            import study_db
            rows = study_db.get_mistakes(limit=5)
            # 按 created_at 从新到旧找该会话的——mistakes 表无 session_id 列，
            # 用"最近 5 条中尚未指定该科目"的最新产品作为目标不可靠；
            # 更稳：直接取最近一条错题（用户刚保存完说"这道是XX的"几乎总是针对最近一条）
            latest = study_db.get_mistakes(limit=1)
            if latest:
                mid = latest[0]["id"]
        except Exception:
            mid = None
    if not mid:
        return "我还没记录到你最近保存的错题，先保存一道再纠正科目吧。"
    try:
        from study_db import update_mistake_subject
        update_mistake_subject(mid, subject)
        _last_saved_mistake[session_id] = mid
        return f"✅ 已把最近保存的错题归到【{subject}】。"
    except Exception as e:
        return f"更正科目失败：{e}"


def _summarize_subject_by_llm(subject: str, items: list, cfg) -> str:
    """按科目聚类错题知识点，返回总结文本。"""
    try:
        qs = "\n".join(f"- {it['question'][:80]}" for it in items[:20])
        prompt = (f"以下是一名学生在《{subject}》上的错题。请把错题聚类成知识点/薄弱点，"
                  f"每个知识点标注覆盖题数，按薄弱程度从高到低排序。"
                  f"直接输出结果，不要客套话。\n{qs}")
        url = cfg["base_url"].rstrip("/")
        if not url.endswith("/chat/completions"):
            url += "/chat/completions"
        resp = requests.post(
            url,
            headers={"Authorization": f"Bearer {cfg['api_key']}", "Content-Type": "application/json"},
            json={"model": cfg["model"],
                  "messages": [{"role": "user", "content": prompt}],
                  "temperature": 0.2, "max_tokens": 800},
            timeout=60
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"].strip()
    except Exception as e:
        logging.error(f"知识点总结失败: {e}")
        return "（总结生成失败，请稍后再试）"


def _backfill_mistake_subjects(cfg, limit: int = 10) -> int:
    """为科目为空的旧错题批量补判科目（每次最多 limit 条）。"""
    try:
        from study_db import get_conn, update_mistake_subject
    except Exception:
        return 0
    conn = get_conn()
    rows = conn.execute("SELECT id, question FROM mistakes WHERE subject='' LIMIT ?",
                        (limit,)).fetchall()
    conn.close()
    n = 0
    for r in rows:
        try:
            s = _detect_subject_by_llm(r["question"], cfg) or ""
        except Exception:
            s = ""
        if s:
            update_mistake_subject(r["id"], s)
            n += 1
    return n


def _handle_summary(session_id: str, cfg=None):
    """整理错题知识点：旧数据补判科目 → 按科目 LLM 聚类 → 报告 + 存档 + 回填标签。"""
    if not cfg:
        return "暂时无法调用总结服务，稍后再试。"
    try:
        from study_db import get_conn, save_mistake_summary, update_mistake_knowledge
    except Exception as e:
        return f"整理失败：{e}"
    backfilled = _backfill_mistake_subjects(cfg)
    conn = get_conn()
    rows = conn.execute(
        "SELECT id, subject, question, correct_answer FROM mistakes ORDER BY subject").fetchall()
    conn.close()
    if not rows:
        return "错题本是空的，先保存几道错题吧。"
    by_subj = {}
    for r in rows:
        by_subj.setdefault(r["subject"] or "未分类", []).append(r)
    lines = ["📊 错题薄弱点分析："]
    for subj, items in by_subj.items():
        summary = _summarize_subject_by_llm(subj, items, cfg)
        try:
            save_mistake_summary(subj, summary)
            for it in items:
                update_mistake_knowledge(it["id"], summary[:200])
        except Exception as e:
            logging.error(f"存档总结失败: {e}")
        lines.append(f"\n【{subj}】（{len(items)} 道）：\n{summary}")
    if backfilled:
        lines.append(f"\n（顺带为 {backfilled} 条旧错题补判了科目）")
    lines.append("\n说明：报告已存档，学习规划师制定计划时会优先安排薄弱知识点。")
    return "\n".join(lines)


def _handle_paper_parse(session_id: str, user_input: str):
    """真题解析：从输入中提取科目与文件名，调用 toolkit.parse_exam_paper。
    无法解析出科目/文件时给出引导。"""
    try:
        import study_db
        subs = [s["name"] for s in study_db.get_subject_overview()]
        subj = _match_subject(user_input, subs)
        # Match document/image filenames, including the new local OCR formats.
        m = re.search(r"([^\s，。]+\.(?:pdf|docx|pptx|txt|md|png|jpe?g|gif|bmp|tiff?|webp))\b", user_input, re.I)
        fname = m.group(1).strip() if m else ""
        if not subj:
            hint = "、".join(subs) if subs else "（还没有科目，请先上传课件建立科目）"
            return (f"请告诉我是哪个科目的真题（现有科目：{hint}），"
                    f"例如：解析高等数学的真题 2024真题.pdf")
        if not fname:
            return "请把真题文件名告诉我，例如：解析真题 2024回忆版.pdf"
        return _toolkit.parse_exam_paper(subj, fname)
    except Exception as e:
        return f"真题解析失败：{e}"


def _handle_plan_view(session_id: str, user_input: str):
    """查看计划：带科目 → 该科独立计划；不带科目 → 全部科目。"""
    try:
        import study_db
        subs = [s["name"] for s in study_db.get_subject_overview()]
        subj = _match_subject(user_input, subs)
        return _toolkit.get_plan_by_subject(subj if subj else "全部")
    except Exception as e:
        return f"查看计划失败：{e}"


def _handle_learning_path(session_id: str, user_input: str):
    """生成学习路径：带科目 → 该科；不带 → 全部。"""
    try:
        import study_db
        subs = [s["name"] for s in study_db.get_subject_overview()]
        subj = _match_subject(user_input, subs)
        return _toolkit.generate_learning_path(subj if subj else "全部")
    except Exception as e:
        return f"生成学习路径失败：{e}"


def _is_review_interrupt(user_input: str) -> bool:
    """重做逐题作答中，判断用户输入是不是"指令"（要打断重做）而不是答案。
    纯数字/短确认词更像答案，不算指令。"""
    t = user_input.strip()
    if not t:
        return False
    if _looks_like_review_stop(t):
        return True
    if _looks_like_master_instruction(t):
        return True
    if _looks_like_review_instruction(t):
        return True
    if re.fullmatch(r"[第]?\s*\d+\s*[道题]?[。.]?", t):
        return False
    if len(t) <= 2 and any(k in t for k in "要嗯好行对是"):
        return False
    return (_looks_like_save_instruction(t)
            or _looks_like_view_instruction(t)
            or _looks_like_summary_instruction(t)
            or _looks_like_subject_correction(t)
            or _looks_like_paper_parse_instruction(t)
            or _looks_like_plan_view_instruction(t)
            or _looks_like_learning_path_instruction(t))


# ---------- 语义理解层（v5：LLM 意图理解，关键词降级为快路径） ----------

_SEMANTIC_INTENTS = """save: 保存错题/存题/记住这道题
view: 查看错题/错题本/我的错题
review: 重做错题/刷题
mark_mastered: 标记某道错题已掌握（无需作答）
summary: 整理/总结错题知识点/薄弱点
subject_correct: 纠正错题所属科目（"这道是XX的"）
paper_parse: 解析上传的真题文件
plan_view: 查看学习计划
learning_path: 询问怎么学/学习路径/学习路线
answer: 当前流程中的作答（保存时给正确答案 / 重做时给题目答案）
confirm: 简短确认（要/嗯/好/行/是）
stop: 停止/退出当前流程
other: 普通答疑、讲题、闲聊等其他一切"""


def _describe_state(session_id):
    """把当前会话的流程状态描述成文字，供 LLM 理解上下文。"""
    parts = []
    if get_pending_context(session_id):
        parts.append("保存流程中：正在等用户给出正确答案")
    if session_id in _pending_review:
        p = _pending_review[session_id]
        if p.get("index", -1) >= 0:
            parts.append(f"重做流程中：正在做第{p['index']+1}道题，等用户作答")
        else:
            parts.append("重做流程中：已列出错题清单，等用户选择题目")
    if session_id in _pending_selection:
        parts.append("保存流程中：已列出多道题，等用户选择编号")
    return "；".join(parts) if parts else "空闲"


def _classify_intent(session_id, last_msg, messages, cfg):
    """LLM 语义理解：结合最近对话与当前状态，判断用户最新消息的意图。
    返回 {"intent":..., "target":..., "answer":...}；调用失败降级为 other。"""
    state = _describe_state(session_id)
    recent = []
    for m in (messages or [])[-7:]:
        if m.get("role") == "system":
            continue
        role = "学生" if m.get("role") == "user" else "助手"
        c = (m.get("content") or "")[:150]
        recent.append(f"{role}: {c}")
    hist = "\n".join(recent[-6:]) if recent else "（无历史）"
    prompt = f"""你是学习助手的意图理解器。根据【当前流程状态】和【最近对话】判断学生最新消息的意图。

当前流程状态：{state}

最近对话：
{hist}

学生最新消息：{last_msg}

可能的意图：
{_SEMANTIC_INTENTS}

判断要点：
1. 【结合上下文理解短回复】学生经常用短句承接上文，你要根据最近对话推断完整意图：
   - 上文在重做错题/选科目，学生只回"高等数学" → review，target 填"高等数学"（重做该科）
   - 上文在查看错题/选科目，学生只回"线性代数" → view，target 填"线性代数"
   - 上文在问"正确答案是什么"，学生回"1/3" → answer
   - 学生回"这道"/"最后一道" → save，target 填 last
2. 状态机内优先考虑：正在等正确答案时，学生给的内容通常是 answer（正确答案）；正在重做作答时，学生给的内容通常是 answer（题目答案）。但如果学生说的是新指令（如"整理错题知识点""查看错题""停止"），要识别为对应指令而不是 answer。
3. "保存这道题/存这道/最后一道" → save，target 填 last
4. "第2道已掌握/标记第2道" → mark_mastered，target 填编号
5. 【科目纠正 vs 描述题型】只有学生明确在**纠正错题的归属科目**（如"这道错题是高等数学的""把它归到线性代数"）才判 subject_correct；如果只是描述题目本身（如"这道题是导数题""这是道证明题"），一律 other（答疑），绝不判 subject_correct！
6. "最短路径怎么求" 是答疑 → other；"怎么学/学习路径/怎么安排" 才是 learning_path
7. 简短"好/嗯/要/是" → confirm
8. 学生在重做/保存流程中直接给数字/公式/答案 → answer
9. 【错题本说法多样化】"错题/错题本/我的错题/把错题调出来/我想看下错题/错题给我看看"等任何想查看错题的表达 → view
10. 【保存说法多样化】"存下来/把刚才那道题存了/记住这道/我想存"等 → save
11. 无法归入上述意图的就是其他学习问题/闲聊 → other

只输出 JSON（不要任何其他文字）：{{"intent":"...","target":"...","answer":""}}
"""
    try:
        url = cfg["base_url"].rstrip("/")
        if not url.endswith("/chat/completions"):
            url += "/chat/completions"
        resp = requests.post(
            url,
            headers={"Authorization": f"Bearer {cfg['api_key']}",
                     "Content-Type": "application/json"},
            json={"model": cfg["model"],
                  "messages": [{"role": "user", "content": prompt}],
                  "temperature": 0.0, "max_tokens": 80},
            timeout=15)
        resp.raise_for_status()
        raw = resp.json()["choices"][0]["message"]["content"].strip()
        raw = re.sub(r"^```json|^```|```$", "", raw).strip()
        data = json.loads(raw)
        return {
            "intent": str(data.get("intent", "other"))[:24],
            "target": str(data.get("target", "")),
            "answer": str(data.get("answer", "")),
        }
    except Exception as e:
        logging.error(f"意图理解失败: {e}")
        return {"intent": "other", "target": "", "answer": ""}


def _semantic_route(session_id, last_msg, messages, cfg):
    """意图分发入口：
    - 状态机内（重做/保存/选择）：100% 本地判断，零 LLM 延迟——系统记得当前流程，
      用户的编号/科目名/短指令/答案都按当前上下文直接接上；
    - 空闲状态：强指令词快路径直出；模糊表达才调 LLM 理解。
    返回 (intent, target)。"""
    t = (last_msg or "").strip()
    # 0) 待机理解：先看上一轮 AI 回复抛出了什么问题（系统记得自己问过什么）
    qtype = _last_reply_question_type(session_id)
    if qtype == "date":
        # 上轮问日期，用户回的像是日期 → 进入考试日期处理
        if _looks_like_date_answer(t):
            return "exam_date", _find_subject_in_context(session_id)
    elif qtype == "subject":
        # 上轮问科目，用户回科目名 → 选科
        subs = _get_subject_names()
        if subs and len(t) <= 8 and _match_subject(t, subs):
            return "subject_choice", _match_subject(t, subs)
    elif qtype == "textbook":
        # 上轮问教材，用户回教材名/不知道 → 教材建库（目标=科目+教材文本）
        subj = _find_subject_in_context(session_id)
        if t and len(t) <= 20 and not _looks_like_review_instruction(t):
            return "textbook_build", f"{subj}|{t}"
    elif qtype == "number":
        # 上轮问第几道，用户回编号 → 选择
        if re.fullmatch(r"[第]?\s*\d+\s*[道题]?[。.]?", t):
            return "selection", t
    elif qtype == "confirm":
        # 上轮问"要吗/好吗"，用户回确认词 → confirm
        if t in ("要", "好", "嗯", "行", "可以", "对", "是", "是的", "没问题") or            t in ("不要", "不用", "算了", "不需要"):
            return "confirm", ""
    elif qtype == "knowledge":
        # 上轮讲解后问"懂了吗"，用户回答 → 普通对话（懂/不懂都接住）
        if t in ("懂了", "明白了", "知道了", "好", "嗯") or            any(k in t for k in ["还是不懂", "没懂", "没听懂", "不理解", "再讲", "换个说法", "为什么"]):
            return "other", ""  # 走普通对话，结合上下文继续
    # 追问机制：上一轮是讲解（非问句），用户追问 → 答疑
    if qtype == "none" and any(k in t for k in ["还是不懂", "没听懂", "没理解", "再讲一遍",
                                                "换个说法", "不太懂", "没明白", "为什么"]):
        return "other", ""
    # 1) 停���词（任何流程中）
    if _looks_like_review_stop(t):
        return "stop", ""
    # 2) 重做流程中（清单已列 index=-1 或逐题 index>=0）
    if session_id in _pending_review:
        p = _pending_review[session_id]
        subs = _get_subject_names()
        # 2a) 科目名 → 重做该科（切科）
        if subs and len(t) <= 8:
            subj = _match_subject(t, subs)
            if subj:
                return "review", subj
        # 2b) 逐题作答中：编号/答案 → answer（作答）
        if p.get("index", -1) >= 0:
            if re.fullmatch(r"[第]?\s*\d+\s*[道题]?[。.]?", t):
                return "answer", t
            if len(t) <= 3 and any(k in t for k in "要嗯好行对是"):
                return "answer", t
            if any(k in t for k in ["跳过", "下一题", "下一道", "换一题", "先跳过", "过"]):
                return "answer", t
            # 指令词（重做中打断）：先查强指令，再默认当作答案
            if (_looks_like_master_instruction(t) or _looks_like_summary_instruction(t)
                    or _looks_like_save_instruction(t) or _looks_like_view_instruction(t)
                    or _looks_like_review_instruction(t) or _looks_like_subject_correction(t)
                    or _looks_like_paper_parse_instruction(t)
                    or _looks_like_plan_view_instruction(t)
                    or _looks_like_learning_path_instruction(t)):
                return _classify_instruction(t)
            return "answer", t  # 默认：在逐题中给的就是答案
        # 2c) 清单已列（index=-1）：编号→开始某道；"我要重做/开始/做"→从第一道开始
        if re.fullmatch(r"[第]?\s*\d+\s*[道题]?[。.]?", t):
            return "review", t
        if any(k in t for k in ["我要重做", "开始", "开始做", "重做", "做", "开做", "刷", "继续", "从头"]):
            return "review", "start"
        if _looks_like_master_instruction(t):
            return "mark_mastered", t
        if (_looks_like_summary_instruction(t) or _looks_like_save_instruction(t)
                or _looks_like_view_instruction(t) or _looks_like_subject_correction(t)
                or _looks_like_paper_parse_instruction(t)
                or _looks_like_plan_view_instruction(t)
                or _looks_like_learning_path_instruction(t)):
            return _classify_instruction(t)
        # 清单刚列出的其他回复：默认当作"开始做"
        return "review", "start"
    # 3) 保存流程“等正确答案”：答案/跳过 → answer；指令 → 打断
    if get_pending_context(session_id):
        ctx = get_pending_context(session_id)
        # 错题本"待选科"状态 → 科目名/编号都归 view 流程（由 _handle_await_subject 处理）
        if ctx.get("action") == "await_subject":
            return "view", ""
        if t in ("跳过", "不写", "没有", "无") or len(t) <= 40:
            if (_looks_like_summary_instruction(t) or _looks_like_view_instruction(t)
                    or _looks_like_review_instruction(t) or _looks_like_save_instruction(t)
                    or _looks_like_subject_correction(t) or _looks_like_master_instruction(t)
                    or _looks_like_paper_parse_instruction(t)
                    or _looks_like_plan_view_instruction(t)
                    or _looks_like_learning_path_instruction(t)):
                return _classify_instruction(t)
        return "answer", t
    # 4) 多题选择中：编号/全部/这道 → selection；指令 → 打断
    if session_id in _pending_selection:
        if (re.fullmatch(r"[第]?\s*\d+\s*[道题]?[。.]?", t)
                or t in ("全部", "所有", "都")
                or any(k in t for k in ["这道", "最后一道", "最后一题"])):
            return "selection", t
        if (_looks_like_summary_instruction(t) or _looks_like_view_instruction(t)
                or _looks_like_review_instruction(t) or _looks_like_save_instruction(t)
                or _looks_like_subject_correction(t) or _looks_like_master_instruction(t)
                or _looks_like_paper_parse_instruction(t)
                or _looks_like_plan_view_instruction(t)
                or _looks_like_learning_path_instruction(t)):
            return _classify_instruction(t)
        return "selection", t
    # 5) 空闲状态
    # 5a) 极短确认/口语确认词（要/嗯嗯/好嘞/行吧/嗯嗯好的）
    if (len(t) <= 3 and any(k in t for k in "要嗯好行对是")) or \
       any(k in t for k in ["嗯嗯", "好的", "好嘞", "好呀", "行吧", "行啊", "可以呀", "没问题", "OK", "ok"]):
        return "confirm", ""
    # 5a2) 口语日期/考试时间（'9.1考'/'下午考'/'明天'，待机回答日期时）
    if _looks_like_date_answer(t):
        return "exam_date", _find_subject_in_context(session_id)
    # 5b) 计划/学习路径意图（先于科目名判断，避免"高数计划"被科目名抢成 view；
    #      生成/重排优先于查看——"搞个计划"也是生成）
    if _looks_like_generate_plan_instruction(t):
        return "generate_plan", t
    if _looks_like_plan_view_instruction(t):
        return "plan_view", t
    if _looks_like_learning_path_instruction(t):
        return "learning_path", t
    # 5b2) 科目纠正（"这道是XX的/这个属于XX"）优先于纯科目名：
    #       "这道是离散数学的"应纠正科目，而不是被"离散数学"当成查看该科。
    if _looks_like_subject_correction(t):
        return "subject_correct", t
    # 5c) 科目名短回复 → 查看该科错题（纯科目名，无其他指令词）
    subs = _get_subject_names()
    if subs and len(t) <= 8:
        subj = _match_subject(t, subs)
        if subj:
            return "view", subj
    # 5d) 绝对无歧义的强指令快路径（这些说法只有一种理解，省一次 LLM）
    if _looks_like_paper_parse_instruction(t):
        return "paper_parse", t
    if _looks_like_review_instruction(t):
        return "review", t
    if _looks_like_master_instruction(t):
        return "mark_mastered", t
    if _looks_like_summary_instruction(t):
        return "summary", t
    # 5e) 高频无歧义的错题/保存说法（"错题本/我的错题/查看错题/保存错题"等，
    #     这些是强指令，只有一种理解，不需要 LLM）
    if _looks_like_view_instruction(t):
        return "view", t
    if _looks_like_save_instruction(t):
        return "save", t
    # 5e2) 口语编号选择："第3题那个/第3道"（有编号优先，可能是选择/作答）
    if re.fullmatch(r"第\s*\d+\s*[题道]\s*(那个|这个|吧|啊)?", t):
        return "selection", t
    # 5e3) 口语指代："就那道/那个/这道"（无动词）→ 保存最近题目
    if any(k in t for k in ["就那道", "就这个", "那个", "这个", "这道", "刚才那道"]) and len(t) <= 10:
        return "save", t
    # 5f) 其他（科目纠正/模糊表达）→ 全部交给 LLM 语义理解。
    #      不在这里用关键词判断 subject_correct：
    #      - "这道题是导数的" 可能是描述题型而非科目纠正 → 必须 LLM 结合上下文
    try:
        r = _classify_intent(session_id, last_msg, messages, cfg)
        return r["intent"], r["target"]
    except Exception:
        return "other", ""


def _classify_instruction(text: str):
    """状态机内打断时，用本地关键词判断是哪个指令（不调 LLM）。"""
    if _looks_like_master_instruction(text):
        return "mark_mastered", ""
    if _looks_like_summary_instruction(text):
        return "summary", ""
    if _looks_like_save_instruction(text):
        return "save", ""
    if _looks_like_view_instruction(text):
        return "view", ""
    if _looks_like_review_instruction(text):
        return "review", ""
    if _looks_like_subject_correction(text):
        return "subject_correct", ""
    if _looks_like_paper_parse_instruction(text):
        return "paper_parse", ""
    if _looks_like_plan_view_instruction(text):
        return "plan_view", ""
    if _looks_like_learning_path_instruction(text):
        return "learning_path", ""
    return "other", ""


def _chat_with_tools(messages, cfg, session_id: str = None, max_iters=5):
    """带工具的对话循环：语义理解（LLM）路由 + 状态机处理。"""
    # 检查是否是“保存”相关指令或处于保存/重做流程中
    if messages and messages[-1].get("role") == "user":
        last_msg = messages[-1].get("content", "")
        if session_id:
            # 0) 语义路由：先快路径，模糊/状态机内走 LLM
            intent, target = _semantic_route(session_id, last_msg, messages, cfg)

            # 1) 停止：彻底退出重做/保存/选择流程
            if intent == "stop":
                _pending_review.pop(session_id, None)
                clear_pending_context(session_id)
                _pending_selection.pop(session_id, None)
                return "好的，已退出当前流程。随时说「重做」「保存错题」或「错题」继续～"

            # 1.5) 错题本"待选科"状态：回科目名 → 展开该科
            r = _handle_await_subject(session_id, last_msg)
            if r:
                return r
            # 1.6) 待机理解路由：语义层判定为"回答考试日期" → 本地解析入库（确定性）
            if intent == "exam_date":
                r = _handle_await_exam_date(session_id, last_msg)
                if r:
                    return r
            # 1.6b) 待机理解路由：上轮问科目，用户回科目名 → 查看该科错题
            if intent == "subject_choice" and target:
                result = _handle_view_mistakes(session_id, "查看" + target + "错题")
                if result:
                    return result
            # 1.6c) 待机理解路由：上轮问编号，用户回编号 → 多题选择
            if intent == "selection" and session_id in _pending_selection:
                r = _handle_selection(session_id, last_msg, cfg)
                if r:
                    return r
            # 1.7) 已在"等考试日期"状态（如用户先报日期、后补日期）
            if get_pending_context(session_id):
                ctx_now = get_pending_context(session_id)
                if ctx_now.get("action") == "await_exam_date":
                    r = _handle_await_exam_date(session_id, last_msg)
                    if r:
                        return r

            # 2) 保存流程“等正确答案”：answer/confirm/selection 视为作答
            if get_pending_context(session_id):
                if intent in ("answer", "confirm", "selection") or last_msg.strip() in ("跳过", "不写", "没有", "无"):
                    r = _handle_pending_answer(session_id, last_msg, messages, cfg)
                    if r:
                        return r
                else:
                    # 新指令打断保存流程
                    clear_pending_context(session_id)

            # 3) 重做逐题作答：answer/confirm/selection 视为答案
            if (session_id in _pending_review
                    and _pending_review[session_id].get("index", -1) >= 0):
                if intent in ("answer", "confirm", "selection"):
                    r = _handle_review_answer(session_id, last_msg, cfg)
                    if r:
                        return r
                else:
                    # 新指令打断重做：退出逐题、保留清单，走下方指令处理
                    _pending_review[session_id]["index"] = -1

            # 4) 多题选择（编号 / 全部 / 直接发新题）——仅在语义路由判定为选择类时才处理，
            #    避免把"整理错题知识点"等打断指令误当新题保存
            if (intent in ("selection", "save", "review", "mark_mastered")
                    and session_id in _pending_selection):
                r = _handle_selection(session_id, last_msg, cfg)
                if r:
                    return r

            # 5) 按语义意图路由（复用现有处理器）
            if intent == "subject_correct":
                r = _handle_subject_correction(session_id, last_msg, cfg)
                if r:
                    return r
            elif intent == "save" and _looks_like_save_instruction(last_msg):
                result = _handle_save_question(session_id, messages, cfg, last_msg)
                if result:
                    return result
            elif intent == "review":
                # 语义层给出 target：科目名（重做该科）/ start（从头开始）
                if session_id in _pending_review:
                    _pending_review[session_id]["index"] = -1
                if target and _match_subject(target, _get_subject_names()):
                    result = _handle_review(session_id, "重做" + target, cfg)
                    if result:
                        return result
                if target == "start":
                    result = _handle_review_select(session_id, "start")
                    if result:
                        return result
                result = _handle_review_select(session_id, last_msg)
                if result:
                    return result
                result = _handle_review(session_id, last_msg, cfg)
                if result:
                    return result
            elif intent == "mark_mastered":
                result = _handle_mark_mastered(session_id, last_msg)
                if result:
                    return result
            elif intent == "summary":
                result = _handle_summary(session_id, cfg)
                if result:
                    return result
            elif intent == "view":
                # 语义层给出 target 科目（如空闲时回"高等数学"）→ 查看该科错题
                if target and _match_subject(target, _get_subject_names()):
                    result = _handle_view_mistakes(session_id, "查看" + target + "错题")
                else:
                    result = _handle_view_mistakes(session_id, last_msg)
                if result:
                    return result
            elif intent == "paper_parse":
                result = _handle_paper_parse(session_id, last_msg)
                if result:
                    return result
            elif intent == "plan_view":
                result = _handle_plan_view(session_id, last_msg)
                if result:
                    return result
            elif intent == "generate_plan":
                # 多科场景必须明确科目，避免旧资料库污染新的计划。
                subjects = _get_subject_names()
                subject = _subject_for_plan_request(last_msg)
                all_subjects = "全部" in last_msg
                if not subject and len(subjects) > 1 and not all_subjects:
                    return "你想生成哪一科的复习计划？请回复科目名；说「全部科目计划」才会统筹排期。"
                if not subject and len(subjects) == 1 and not all_subjects:
                    subject = subjects[0]
                if not subject and not all_subjects:
                    return ("请先告诉我科目名和考试日期，例如「高等数学，12月20日考，帮我制定复习计划」。"
                            "我会先为这门课建库；没有资料时可联网查课程大纲。")
                # 对新科目：用户若在一句话里给了日期，先登记日期并联网建立知识块；
                # 日期缺失时 set_exam_date 会返回清晰提示，不会错误使用旧科目的库。
                import study_db
                sid = study_db.get_subject_id_by_name(subject) if subject else None
                if not sid and not all_subjects:
                    date_result = _toolkit.set_exam_date(subject, last_msg)
                    if not date_result.startswith("✅"):
                        # 记住新科目，下一句仅回复“12月20日”时仍能继续建库。
                        set_pending_context(session_id, "await_exam_date", {"subject": subject})
                        return date_result
                    build_result = _toolkit.build_from_textbook(subject)
                    if not build_result.startswith("✅"):
                        return build_result
                elif sid:
                    conn = study_db.get_conn()
                    count = conn.execute("SELECT COUNT(*) AS c FROM blocks WHERE subject_id=?", (sid,)).fetchone()["c"]
                    conn.close()
                    if count == 0:
                        build_result = _toolkit.build_from_textbook(subject)
                        if not build_result.startswith("✅"):
                            return build_result
                result = _toolkit.generate_study_plan(
                    None, "用户请求生成计划", "" if all_subjects else subject)
                if result:
                    return result
            elif intent == "textbook_build":
                # target 格式 "科目|教材名"（"|不知道/没有" 表示无教材 → 自动搜公共大纲）
                parts = target.split("|", 1) if target else ["", ""]
                subj = parts[0]
                tb = parts[1] if len(parts) > 1 else ""
                if not subj:
                    subs = _get_subject_names()
                    subj = subs[0] if subs else ""
                if not subj:
                    return "请先告诉我科目名（如：高等数学），我再帮你建库排计划。"
                if tb in ("不知道", "没有", "没有教材", "随便", "不懂", "不清楚", "不知道用啥", ""):
                    tb = ""
                result = _toolkit.build_from_textbook(subj, tb)
                if result:
                    return result
            elif intent == "learning_path":
                result = _handle_learning_path(session_id, last_msg)
                if result:
                    return result
            # confirm / other / answer(空闲时) → 落到正常大模型对话
        else:
            # 没有 session_id，降级处理
            if _looks_like_save_instruction(last_msg):
                return "请先刷新页面重试，或者直接说“记录错题：题目xxx，错误答案xxx，正确答案xxx”来手动保存。"

    # 正常的大模型对话流程
    url = cfg["base_url"].rstrip("/")
    if not url.endswith("/chat/completions"):
        url += "/chat/completions"

    # 关闭云模型后仍保留确定性功能（查看计划、今日任务、错题回顾等），
    # 但不允许本模块把题目、答案或资料片段发往在线模型。
    if not get_preferences()["allow_cloud_model"]:
        return "已关闭在线模型。你仍可从主页查看资料、修改考试排期和读取今日计划；如需智能答疑或解析，请在学习设置中明确允许在线模型。"
    for _ in range(max_iters):
        resp = requests.post(url, headers={
            "Authorization": f"Bearer {cfg['api_key']}",
            "Content-Type": "application/json"},
            json={"model": cfg["model"], "messages": messages,
                  "tools": TOOLS, "temperature": 0.3, "max_tokens": 4000,
                  **({"thinking": {"type": "disabled"}}
                     if cfg["model"].lower().startswith("deepseek") else {})},
            timeout=120)
        resp.raise_for_status()
        choice = resp.json()["choices"][0]
        if choice.get("finish_reason") == "length":
            raise ValueError("模型输出达到长度上限，答案未完整生成，请缩小问题范围后重试")
        msg = choice["message"]
        tool_calls = msg.get("tool_calls")
        if not tool_calls:
            reply = (msg.get("content") or "").strip()
            if not reply:
                raise ValueError("模型没有返回答案，请稍后重试")
            # 若本次回复在问考试日期 → 记住"等日期"状态（科目从对话上文提取）
            # 注意：直接判断本轮 reply 文本，不能依赖 _session_answers——
            # 该轮回复尚未记录进缓存（_add_answer 由外层在返回后才调用），
            # 否则第一条"什么时候考"后 await_exam_date 状态永远设置不上。
            if session_id and _reply_asks_exam_date(reply):
                subj = _find_subject_in_context(session_id)
                if subj:
                    set_pending_context(session_id, "await_exam_date",
                                        {"subject": subj}, timeout_seconds=600)
            return reply
        messages.append(msg)
        for tc in tool_calls:
            fname = tc["function"]["name"]
            try:
                args = json.loads(tc["function"].get("arguments") or "{}")
            except Exception:
                args = {}
            try:
                result = FUNC_MAP[fname](**args) if fname in FUNC_MAP else f"未知工具 {fname}"
            except Exception as e:
                result = f"工具执行出错：{e}"
            messages.append({"role": "tool", "tool_call_id": tc["id"], "content": str(result)})
    return "这个问题我处理起来有点绕，你换个说法试试？"

def try_fast_channel(query: str, history=None, session_id: str = None,
                     uploaded_files=None, force=False) -> str | None:
    """入口：学习类日常消息返回回复文本；不该走快通道则返回 None。
    注意：用户消息已在 search.py 统一调用 _add_question 记录到会话缓存，此处不再重复记录。"""
    if not force and not _should_fast(query, session_id, uploaded_files):
        return None

    from datetime import date
    from study_preferences import get_preferences as _preferences
    # Reading existing records is a local operation, even when cloud analysis is enabled.
    text = query.strip()
    notebook_link = "\n\n[打开错题本：筛选、查看答案、手动录入与自主重做](/cosight/mistakes.html)"
    if ("错题" in text and any(word in text for word in ("查看", "看看", "看一下", "看下", "打开", "显示", "查询", "列出"))) or text in ("错题", "错题本", "我的错题"):
        reply = _handle_view_mistakes(session_id or "local", text) + notebook_link
        if session_id:
            _add_answer(session_id, reply)
        return reply
    context = get_pending_context(session_id) if session_id else None
    if context and context.get("action") == "await_subject" and _match_subject(text, context.get("data", {}).get("subjects", [])):
        reply = _handle_await_subject(session_id, text) + notebook_link
        _add_answer(session_id, reply)
        return reply
    if not _preferences()["allow_cloud_model"] and "错题" in text and any(word in text for word in ("重做", "练习", "复习")):
        reply = "可以在错题本里遮住答案先做题，再对照参考答案自行标记结果；这不需要在线模型。AI讲解与自动批改仍需要单独授权。" + notebook_link
        if session_id:
            _add_answer(session_id, reply)
        return reply
    if not _preferences()["allow_cloud_model"]:
        # Do not invoke semantic routing or configuration discovery when consent is off.
        if any(word in query for word in ("今日", "今天", "任务")):
            return _toolkit.get_today_tasks()
        if any(word in query for word in ("进度", "总览")):
            return _toolkit.get_study_overview()
        if "计划" in query and any(word in query for word in ("查看", "看看", "我的")):
            return _toolkit.get_plan_by_subject("全部")
        return "当前账号尚未开启在线答疑。请点击页面顶部「在线答疑：未开启」，在学习设置中勾选允许在线模型并保存。开启后，题目和必要资料片段会发送给配置的在线模型。查看错题、资料和已有计划不需要开启。"
    cfg = ma.sniff_llm_config()
    messages = [{"role": "system", "content": SYSTEM_PROMPT.format(today=date.today().isoformat())}]
    if uploaded_files:
        messages.append({"role": "system", "content": "本轮可用的当前用户资料（只可读取这些已授权路径）：" + json.dumps(uploaded_files, ensure_ascii=False)})

    # 生成/查看计划且带了上传真题 → 先自动解析，让计划引用到刚传的文件
    plan_hint = ""
    if (uploaded_files and (
            _looks_like_generate_plan_instruction(query)
            or _looks_like_plan_view_instruction(query)
            or _looks_like_learning_path_instruction(query))):
        plan_hint = _auto_parse_uploaded_papers(query, uploaded_files)
    
    # 用后端缓存的历史轮次（用户+AI 交错）作为上下文——让 LLM 能理解
    # "9月1号" 是对上一轮"哪天考高数"的回答，而不是独立任务
    if session_id:
        for turn in _get_session_turns(session_id, max_turns=6):
            messages.append({"role": turn["role"], "content": turn["content"]})
    elif history:
        for h in history[-6:]:
            role = "user" if h.get("from") == "human" else "assistant"
            texts = [i.get("value", "") for i in h.get("initData", []) if isinstance(i, dict) and i.get("type") == "text"]
            if texts:
                messages.append({"role": role, "content": " ".join(texts)[:2000]})
    messages.append({"role": "user", "content": query})

    reply = _chat_with_tools(messages, cfg, session_id)
    # 记录 AI 回复（供下一轮对话理解上下文）
    if session_id and isinstance(reply, str):
        _add_answer(session_id, reply)
    if plan_hint and isinstance(reply, str):
        reply = plan_hint + reply
    return reply


def _auto_parse_uploaded_papers(query: str, uploaded_files: list) -> str:
    """生成计划/查看计划等场景：若消息带了刚上传的真题文件且该科尚未解析，
    自动调用 parse_exam_paper 解析入库，让计划能引用到真题。
    返回解析结果提示（空字符串表示无需解析/已解析）。"""
    if not uploaded_files:
        return ""
    import os as _os
    try:
        import study_db
        subs = [s["name"] for s in study_db.get_subject_overview()]
        subj = _match_subject(query, subs)
        if not subj:
            return ""
        # 该科是否已有真题
        cnt = study_db.get_exam_questions_by_subject(
            subject_id=study_db.get_subject_id_by_name(subj))
        if cnt:
            return ""  # 已解析过，跳过
        # 取第一个 pdf 文件（上传文件可能是文件名或URL）
        fname = None
        for f in uploaded_files:
            n = _os.path.basename(str(f).rstrip("/").split("/")[-1])
            if n.lower().endswith(".pdf"):
                fname = n
                break
        if not fname:
            return ""
        result = _toolkit.parse_exam_paper(subj, fname)
        return f"（已自动解析上传的真题《{fname}》：{result[:120]}）\n"
    except Exception as e:
        logging.warning(f"自动解析上传真题失败: {e}")
        return ""
from study_preferences import get_preferences


# Confirmation and numeric selection require a real pending save context.
def _looks_like_save_instruction(text):
    return bool(re.search(r"保存|存这道|存一下|存起来|记录错题|记住这道|记入错题|记到错题|添加.*错题", text or ""))
