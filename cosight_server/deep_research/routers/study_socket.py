"""Authenticated learning WebSocket. Responses only return to their originating connection."""
import json
import time
import uuid
import asyncio
import threading
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, HTTPException
from cosight_server.deep_research.services.account_service import COOKIE_NAME, read_session
from cosight_server.deep_research.services.study_gateway import answer

studySocketRouter = APIRouter()


@studySocketRouter.websocket("/api/openans-support-chatbot/v1/robot/wss/messages")
async def study_socket(socket: WebSocket):
    await socket.accept()
    try:
        while True:
            data = await socket.receive_json()
            if not read_session(socket.cookies.get(COOKIE_NAME)):
                await socket.close(code=4401)
                return
            if not isinstance(data, dict) or not isinstance(data.get("topic"), str) or not 1 <= len(data["topic"]) <= 160:
                await socket.close(code=4400)
                return
            topic = data["topic"]
            # No shared global topic registry: subscribing cannot redirect another user's response.
            if data.get("action") == "subscribe":
                continue
            if data.get("action") != "message":
                continue
            try:
                message = json.loads(data.get("data", ""))
                info = message.get("sessionInfo") or {}
                extra = message.get("extra") or {}
                backend = extra.get("fromBackEnd") or {}
                params = {"content": message.get("initData"),
                          "sessionInfo": {"sessionId": info.get("learningSessionId") or topic},
                          "uploadedFiles": backend.get("uploadedFiles", []),
                          "replay": bool(extra.get("replay") or backend.get("replay"))}
                await socket.send_json({"topic": topic, "data": {"type": "multi-modal", "from": "human",
                    "uuid": str(uuid.uuid4()), "timestamp": int(time.time()*1000),
                    "changeType": "replace", "initData": message.get("initData"), "status": "in_progress"}})
                events = asyncio.Queue()
                loop = asyncio.get_running_loop()
                cancelled = threading.Event()
                def emit(event):
                    if not cancelled.is_set():
                        loop.call_soon_threadsafe(events.put_nowait, event)
                job = asyncio.create_task(answer(params, on_event=emit, cancelled=cancelled.is_set))
                started_wait = last_notice = time.monotonic()
                try:
                    while not job.done() or not events.empty():
                        if not read_session(socket.cookies.get(COOKIE_NAME)):
                            cancelled.set()
                            await socket.close(code=4401)
                            return
                        try:
                            event = await asyncio.wait_for(events.get(), timeout=.2)
                        except asyncio.TimeoutError:
                            if time.monotonic() - last_notice >= 10:
                                await socket.send_json({"topic": topic, "data": {"type": "study-agent-progress",
                                    "stage": "waiting", "elapsed_seconds": int(time.monotonic()-started_wait)}})
                                last_notice = time.monotonic()
                            continue
                        if not read_session(socket.cookies.get(COOKIE_NAME)):
                            cancelled.set()
                            await socket.close(code=4401)
                            return
                        await socket.send_json({"topic": topic, "data": event})
                    reply = await job
                finally:
                    if not job.done():
                        cancelled.set()
                        # Keep the per-account lock until its worker has actually stopped.
                        try:
                            await asyncio.shield(job)
                        except Exception:
                            pass
            except HTTPException as error:
                reply = error.detail
            except (ValueError, TypeError, AttributeError):
                reply = "消息格式不正确，请刷新后重试"
            except Exception:
                import logging
                logging.getLogger(__name__).exception("学习消息处理失败")
                reply = "处理失败，请稍后重试；首次使用请确认服务器模型配置。"
            # Logout during generation invalidates the response too.
            if not read_session(socket.cookies.get(COOKIE_NAME)):
                await socket.close(code=4401)
                return
            await socket.send_json({"topic": topic, "data": {"type": "multi-modal", "from": "ai",
                "uuid": str(uuid.uuid4()), "timestamp": int(time.time()*1000), "changeType": "append",
                "initData": [{"type": "text", "value": str(reply)}], "status": "finished_successfully"}})
            await socket.send_json({"topic": topic, "data": {"type": "control-status-message",
                "initData": {"status": "finished_successfully"}}})
    except WebSocketDisconnect:
        return
