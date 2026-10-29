# -*- coding: utf-8 -*-
"""
material_analyst.py —— 资料分析员（阶段2）
放置位置：Co-Sight 项目根目录（和 study_db.py 同一层）

用法：
  1) 先测大模型连接：   python material_analyst.py --check
  2) 正式解析资料：     python material_analyst.py "离散数学" 2026-07-12 课件.pdf --paper 往年卷.pdf
     （课件PDF可以给多个；--paper 后面跟往年卷，可选但强烈建议给）

功能：读PDF → 大模型拆知识块 → 分析往年卷建题型画像 → 写入 study.db
依赖：pip install pypdf requests
"""

import os
import re
import sys
import json
import argparse
import requests

import study_db  # 复用阶段1的数据层

ROOT = os.path.dirname(os.path.abspath(__file__))

# ---------------- 第一部分：读取 .env 并自动嗅探大模型配置 ----------------

# 常见的变量命名候选（按优先级）。Co-Sight 的 .env 用的是其中某一套。
KEY_CANDIDATES = {
    "api_key":  ["API_KEY", "OPENAI_API_KEY", "LLM_API_KEY", "TOOL_API_KEY",
                 "PLAN_API_KEY", "ACT_API_KEY", "DEEPSEEK_API_KEY"],
    "base_url": ["API_BASE_URL", "BASE_URL", "OPENAI_BASE_URL", "LLM_BASE_URL",
                 "TOOL_BASE_URL", "PLAN_BASE_URL", "ACT_BASE_URL"],
    "model":    ["MODEL_NAME", "LLM_MODEL_NAME", "MODEL", "OPENAI_MODEL",
                 "TOOL_MODEL_NAME", "PLAN_MODEL_NAME", "ACT_MODEL_NAME"],
}


def load_env(path=None):
    """手动解析 .env 文件为字典（不依赖第三方库）。"""
    path = path or os.path.join(ROOT, ".env")
    env = dict(os.environ)
    if not os.path.exists(path):
        return env
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            env.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    return env


def sniff_llm_config():
    """从 .env 中嗅探出 api_key / base_url / model 三件套。"""
    env = load_env()
    cfg = {}
    for want, candidates in KEY_CANDIDATES.items():
        for name in candidates:
            if env.get(name):
                cfg[want] = env[name]
                cfg[want + "_from"] = name
                break
    missing = [k for k in ("api_key", "base_url", "model") if k not in cfg]
    if missing:
        print("[错误] 在 .env 里没找到这些配置：", missing)
        print("       你的 .env 里实际存在的变量名有：")
        for k in env:
            print("        -", k)
        print("       请把上面这份变量名清单发给 AI（注意：只发变量名，不要发等号后面的值！）")
        raise RuntimeError("服务器尚未配置模型，请管理员设置 API_KEY、API_BASE_URL 和 MODEL_NAME")
    return cfg


def chat(messages, cfg, temperature=0.2, max_tokens=8000, timeout=300):
    """调用 OpenAI 兼容接口。返回模型输出的文本。
    兼容 DeepSeek V4 思考模式：内容为空时尝试从思考字段取，
    仍为空则自动重试一次，再失败打印原始返回帮助排查。"""
    from user_context import current_user
    from study_preferences import get_preferences
    if current_user.get() and not get_preferences()["allow_cloud_model"]:
        raise PermissionError("当前账号未允许在线模型分析")
    url = cfg["base_url"].rstrip("/")
    if not url.endswith("/chat/completions"):
        url = url + "/chat/completions"
    last_data = None
    for attempt in (1, 2):
        resp = requests.post(
            url,
            headers={"Authorization": f"Bearer {cfg['api_key']}",
                     "Content-Type": "application/json"},
            json={"model": cfg["model"], "messages": messages,
                  "temperature": temperature, "max_tokens": max_tokens},
            timeout=timeout)
        resp.raise_for_status()
        last_data = resp.json()
        msg = last_data["choices"][0]["message"]
        content = (msg.get("content") or "").strip()
        if last_data['choices'][0].get('finish_reason') == 'length':
            raise ValueError('模型输出达到长度上限，结果未完整生成')
        if content:
            return content
        print(f"      [警告] 模型第{attempt}次返回为空，"
              f"finish_reason={last_data['choices'][0].get('finish_reason')}，重试...")
    import json as _json
    raise ValueError("模型未返回完整答案，请稍后重试")


def parse_json_from_llm(text):
    """大模型输出的JSON可能带```围栏或多余文字，这里稳健地抠出来。
    用 json.JSONDecoder.raw_decode 精确定位第一个合法 JSON 值，
    避免题目文本里的方括号/花括号（如矩阵 [[1,2],[3,4]]）导致 find/rfind 切错位置。"""
    text = text.strip()
    m = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if m:
        text = m.group(1).strip()
    # 逐位置尝试 raw_decode：跳过围栏/说明文字，找到第一个合法 JSON 起始
    decoder = json.JSONDecoder()
    idx = 0
    n = len(text)
    while idx < n:
        if text[idx] in "[{":
            try:
                obj, _ = decoder.raw_decode(text, idx)
                return obj
            except (json.JSONDecodeError, ValueError):
                pass
        idx += 1
    # 兜底：整段剥离后直接解析（老逻辑）
    start = text.find("[") if text.find("[") != -1 else text.find("{")
    end = max(text.rfind("]"), text.rfind("}"))
    if start == -1 or end == -1:
        raise ValueError("模型输出中找不到JSON：\n" + text[:500])
    return json.loads(text[start:end + 1])


# ---------------- 第二部分：PDF 文本抽取 ----------------

def extract_pdf_text(path, max_chars=30000):
    """抽取 PDF/DOCX/PPTX/TXT/图片文字，扫描页自动使用本地 OCR。"""
    suffix = os.path.splitext(path)[1].lower()
    if suffix == ".pdf":
        from app.cosight.tool.ocr_toolkit import OCRToolkit
        text = OCRToolkit().extract_text(path)
    elif suffix == ".docx":
        from docx import Document
        text = "\n\n".join(p.text for p in Document(path).paragraphs)
    elif suffix == ".pptx":
        from pptx import Presentation
        text = "\n\n".join(
            f"[幻灯片 {i+1}]\n"+"\n".join(shape.text for shape in slide.shapes if hasattr(shape,"text") and shape.text)
            for i,slide in enumerate(Presentation(path).slides)
        )
    elif suffix in {".txt", ".md", ".json"}:
        with open(path, "r", encoding="utf-8", errors="ignore") as source:
            text = source.read()
    elif suffix in {".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff", ".webp", ".gif"}:
        from app.cosight.tool.ocr_toolkit import OCRToolkit
        text = OCRToolkit().extract_text(path)
    else:
        raise ValueError(f"不支持解析 {suffix or '无扩展名'} 文件")
    from learning_agent.material_cache import write as cache_text
    cache_text(path, text)
    text = text[:max_chars]
    if len(text.strip()) < 100:
        print(f"[警告] {os.path.basename(path)} 识别到的文字较少，请检查原文件清晰度。")
    return text


def validate_blocks(payload):
    """在写库前校验并清洗模型 JSON，拒绝残缺/越界结构。"""
    if not isinstance(payload, list):
        raise ValueError("知识块输出必须是 JSON 数组")
    clean = []
    for index, block in enumerate(payload[:30]):
        if not isinstance(block, dict) or not str(block.get("name", "")).strip():
            continue
        raw_prereq = block.get("prereq", [])
        if not isinstance(raw_prereq, list):
            raw_prereq = []
        prereq = [int(item) for item in raw_prereq
                  if isinstance(item, int) and 0 <= item < index]
        try:
            minutes = int(block.get("est_minutes", 60))
        except (TypeError, ValueError):
            minutes = 60
        clean.append({
            "chapter": str(block.get("chapter", "未分章"))[:80],
            "name": str(block["name"]).strip()[:120],
            "prereq": prereq,
            "est_minutes": min(180, max(15, minutes)),
        })
    if len(clean) < 3:
        raise ValueError("模型返回的有效知识块少于 3 个，请重试或上传更完整资料")
    return clean


def validate_profiles(payload, block_count):
    if not isinstance(payload, list):
        raise ValueError("题型画像输出必须是 JSON 数组")
    clean = []
    for profile in payload[:100]:
        if not isinstance(profile, dict) or not isinstance(profile.get("block_index"), int):
            continue
        index = profile["block_index"]
        if not 0 <= index < block_count:
            continue
        try:
            score = float(profile.get("score_value", 0))
        except (TypeError, ValueError):
            score = 0
        clean.append({"block_index": index, "q_type": str(profile.get("q_type", "未知"))[:30],
                      "score_value": min(100, max(0, score)),
                      "frequency": str(profile.get("frequency", ""))[:40]})
    return clean


# ---------------- 第三部分：两步大模型分析 ----------------

BLOCKS_PROMPT = """你是一名资深大学课程分析专家。下面是《{subject}》的课件文本。
请把这门课拆解为10~20个"知识块"（复习的最小单元，粒度示例：把"图论"拆为图的基本概念/图的遍历/最短路算法/最小生成树）。

要求：
1. 按课件的章节顺序排列
2. prereq 填该块依赖的前置块的序号列表（从0开始计），没有就填 []，只允许依赖序号比自己小的块
3. est_minutes 是零基础学生学完该块的预估分钟数（30~90之间）
4. 只输出JSON数组，不要任何其他文字，格式：
[{{"chapter":"章节名","name":"知识块名","prereq":[0,1],"est_minutes":60}}]

课件文本：
{text}"""

PROFILE_PROMPT = """你是一名应试分析专家。下面给出《{subject}》的知识块清单和{source_desc}。
请为每个知识块建立"题型画像"：它在试卷上以什么题型出现、大约多少分、出现频率如何。

要求：
1. 严格依据给出的材料判断，材料里完全没出现的知识块，标 frequency 为"未出现"、score_value 为 0
2. 一个知识块可以对应多种题型（输出多条）
3. 只输出JSON数组，不要任何其他文字，格式：
[{{"block_index":0,"q_type":"选择","score_value":5,"frequency":"3年2考"}}]
   block_index 是知识块在清单中的序号（从0开始）

知识块清单：
{blocks}

材料（{source_desc}）：
{text}"""


def analyze(subject, exam_date, courseware_paths, paper_path, cfg):
    # 1. 抽取课件文本（多份课件拼接）
    print("[1/5] 抽取课件文本...")
    course_text = "\n\n".join(extract_pdf_text(p) for p in courseware_paths)

    # 2. 大模型拆知识块
    print("[2/5] 大模型拆解知识块（约需1-2分钟）...")
    raw = chat([{"role": "user", "content":
                 BLOCKS_PROMPT.format(subject=subject, text=course_text)}], cfg)
    blocks = validate_blocks(parse_json_from_llm(raw))
    print(f"      拆出 {len(blocks)} 个知识块")

    # 3. 题型画像（有往年卷用往年卷，没有就基于课件估计）
    if paper_path:
        print("[3/5] 抽取往年卷文本...")
        src_text, src_desc, src_tag = extract_pdf_text(paper_path), "往年试卷", "往年卷"
    else:
        print("[3/5] 未提供往年卷，将基于课件估计题型画像（建议尽量补充往年卷）")
        src_text, src_desc, src_tag = course_text[:15000], "课件（无往年卷，估计）", "课件估计"

    print("[4/5] 大模型建立题型画像（约需1-2分钟）...")
    blocks_brief = json.dumps(
        [{"index": i, "chapter": b["chapter"], "name": b["name"]}
         for i, b in enumerate(blocks)], ensure_ascii=False)
    raw = chat([{"role": "user", "content":
                 PROFILE_PROMPT.format(subject=subject, source_desc=src_desc,
                                       blocks=blocks_brief, text=src_text)}], cfg)
    profiles = validate_profiles(parse_json_from_llm(raw), len(blocks))
    print(f"      生成 {len(profiles)} 条画像记录")

    # 4. 写入数据库
    print("[5/5] 写入数据库...")
    study_db.init_db()
    sid = study_db.add_subject(subject, exam_date)
    index_to_dbid = {}
    for i, b in enumerate(blocks):
        prereq_dbids = ",".join(str(index_to_dbid[j]) for j in b.get("prereq", [])
                                if j in index_to_dbid)
        bid = study_db.add_block(sid, b.get("chapter", ""), b["name"],
                                 prereq_dbids, int(b.get("est_minutes", 60)))
        index_to_dbid[i] = bid
    conn = study_db.get_conn()
    for p in profiles:
        bidx = p.get("block_index")
        if bidx in index_to_dbid:
            conn.execute(
                """INSERT INTO exam_profile(block_id,q_type,score_value,frequency,source)
                   VALUES (?,?,?,?,?)""",
                (index_to_dbid[bidx], p.get("q_type", "未知"),
                 float(p.get("score_value", 0)), p.get("frequency", ""), src_tag))
    conn.commit()
    conn.close()

    # 5. 打印成果预览
    print("\n========== 解析完成 ✅ ==========")
    print(f"科目：{subject}（考试日期 {exam_date}），科目id={sid}")
    for i, b in enumerate(blocks):
        print(f"  [{i}] {b.get('chapter','')} / {b['name']}  约{b.get('est_minutes',60)}分钟")
    print("题型画像（前10条）：")
    for p in profiles[:10]:
        bidx = p.get("block_index")
        name = blocks[bidx]["name"] if isinstance(bidx, int) and bidx < len(blocks) else "?"
        print(f"  {name}：{p.get('q_type')} / {p.get('score_value')}分 / {p.get('frequency')}")
    print("=================================")


# ---------------- 入口 ----------------

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("subject", nargs="?", help="科目名，如 离散数学")
    ap.add_argument("exam_date", nargs="?", help="考试日期，如 2026-07-12")
    ap.add_argument("courseware", nargs="*", help="课件PDF路径，可多个")
    ap.add_argument("--paper", help="往年卷PDF路径（可选，强烈建议）")
    ap.add_argument("--check", action="store_true", help="只测试大模型连接")
    args = ap.parse_args()

    cfg = sniff_llm_config()
    print(f"[配置] 模型={cfg['model']}（来自{cfg['model_from']}），"
          f"地址来自{cfg['base_url_from']}，Key来自{cfg['api_key_from']}")

    if args.check:
        print("[测试] 正在调用大模型...")
        out = chat([{"role": "user", "content": "请只回复两个字：正常"}], cfg,
                   max_tokens=2000)
        print(f"[测试] 模型回复：{out}")
        print("[OK] 大模型连接正常 ✅ 可以正式解析资料了")
        sys.exit(0)

    if not (args.subject and args.exam_date and args.courseware):
        ap.print_help()
        sys.exit(1)
    analyze(args.subject, args.exam_date, args.courseware, args.paper, cfg)
