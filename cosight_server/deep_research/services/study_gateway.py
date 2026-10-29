"""Multi-user learning gateway: only scoped study tools, no generic shell/research agent."""
import asyncio
import json
from fastapi import HTTPException
from starlette.concurrency import run_in_threadpool
from user_context import scoped_session_id, user_root
import study_db

_locks = {}


def _conversation_conn():
    conn = study_db.get_conn()
    conn.execute("""CREATE TABLE IF NOT EXISTS chat_messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL,
        role TEXT NOT NULL, content TEXT NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP)""")
    conn.commit()
    return conn


def save_message(session_id, role, content):
    with _conversation_conn() as conn:
        conn.execute("INSERT INTO chat_messages(session_id,role,content) VALUES (?,?,?)",
                     (session_id, role, content))


def history(session_id):
    with _conversation_conn() as conn:
        rows = conn.execute("SELECT role,content,created_at FROM (SELECT * FROM chat_messages "
                            "WHERE session_id=? ORDER BY id DESC LIMIT 100) ORDER BY id", (session_id,)).fetchall()
    return [dict(row) for row in rows]


def conversations():
    with _conversation_conn() as conn:
        rows = conn.execute("SELECT session_id,MIN(id) first_id,MAX(id) last_id FROM chat_messages "
                            "GROUP BY session_id ORDER BY last_id DESC LIMIT 50").fetchall()
        result = []
        for row in rows:
            first = conn.execute("SELECT content,created_at FROM chat_messages WHERE id=?", (row["first_id"],)).fetchone()
            result.append({"session_id": row["session_id"].split(":", 1)[-1],
                           "title": first["content"][:80], "created_at": first["created_at"]})
    return result


async def answer(params, on_event=None, cancelled=None):
    if not isinstance(params, dict):
        raise HTTPException(400, "消息格式不正确")
    if params.get("replay"):
        return "多人学习版不运行通用任务回放。请使用聊天历史或查看正式复习计划。"
    content = params.get("content")
    if not isinstance(content, list) or not content or not isinstance(content[0], dict):
        raise HTTPException(400, "消息内容不能为空")
    query = content[0].get("value")
    if not isinstance(query, str) or not query.strip() or len(query) > 16000:
        raise HTTPException(400, "消息须为1—16000字的文本")
    info = params.get("sessionInfo") or {}
    if not isinstance(info, dict):
        raise HTTPException(400, "会话格式不正确")
    sid = scoped_session_id(info.get("sessionId"))
    from app.cosight.tool.study_toolkit import _resolve
    uploads = params.get("uploadedFiles") or []
    if not isinstance(uploads, list) or len(uploads) > 20:
        raise HTTPException(400, "一次最多使用20份资料")
    own_files = []
    for item in uploads:
        identifier = item.get("upload_id") or item.get("libraryUploadId") or item.get("filename") if isinstance(item, dict) else item
        try:
            resolved = _resolve(str(identifier))
            from pathlib import Path
            if not Path(resolved).is_file():
                raise PermissionError
        except (PermissionError, ValueError):
            raise HTTPException(404, "资料不存在或不属于当前账号") from None
        own_files.append(resolved)
    # A busy account cannot exhaust the server by spawning unbounded LLM jobs.
    key = str(user_root())
    lock = _locks.setdefault(key, asyncio.Lock())
    if lock.locked():
        raise HTTPException(409, "上一条消息仍在处理中，请稍候")
    async with lock:
        def run():
            from learning_agent.runtime import run_agent
            previous = history(sid)
            save_message(sid, "user", query)
            reply = run_agent(query, sid, own_files, previous, emit=on_event, cancelled=cancelled)
            save_message(sid, "assistant", str(reply))
            return reply
        return await run_in_threadpool(run)
