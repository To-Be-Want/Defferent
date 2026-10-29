# Copyright 2025 ZTE Corporation.
# All Rights Reserved.
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.

import json
import asyncio
import os
import re
import shutil
import uuid
from datetime import date as date_type, datetime
from pathlib import Path
from typing import Any, List
from fastapi import APIRouter, Body, File, Form, UploadFile, HTTPException
from starlette.requests import Request
from fastapi.responses import StreamingResponse
from urllib.parse import quote, unquote
from cosight_server.sdk.common.config import custom_config
from app.cosight.task.task_manager import TaskManager
from llm import llm_for_plan, llm_for_act, llm_for_tool, llm_for_vision
from cosight_server.deep_research.services.i18n_service import i18n
from cosight_server.deep_research.services.credibility_analyzer import credibility_analyzer
from cosight_server.deep_research.services.study_library import (
    index_existing_uploads, list_materials, material_path, save_material,
    save_study_plan, update_material_subject,
)
from app.common.logger_util import logger
from study_preferences import get_preferences, update_preferences, validate_study_time_slots
import study_db
import scheduler

# 引入CoSight所需的依赖
from app.cosight.task.plan_report_manager import plan_report_event_manager
from app.cosight.task.todolist import Plan
from CoSight import CoSight

searchRouter = APIRouter()


def _upload_root() -> str:
    """用户上传文件的根目录（与 main.py 挂载的静态目录一致）。"""
    from user_context import current_user, user_root
    if current_user.get():
        return str(user_root() / "upload_files")
    env_dir = os.getenv(custom_config.get("upload_dir_env")) if custom_config else None
    return (env_dir + "/upload_files") if env_dir else os.path.join(os.getcwd(), "upload_files")


def copy_uploaded_files_to_workspace(upload_ids: list, workspace_path: str) -> dict:
    """把 upload_files 下的用户上传文件（按 upload_id/文件名/URL 识别）复制到任务工作区。
    upload_ids 元素兼容三种形态：
      - "2024高数真题.pdf"            纯文件名
      - "/api/nae-deep-research/v1/upload_files/2024高数真题.pdf"  完整静态URL
      - {"filename": "xxx.pdf", ...}  前端上传接口返回的对象
    """
    try:
        src_root = _upload_root()
        if not workspace_path:
            workspace_path = os.path.join(os.getcwd(), "work_space")
        os.makedirs(workspace_path, exist_ok=True)
        copied, missing = 0, []
        for uid in upload_ids or []:
            fname = None
            if isinstance(uid, dict):
                fname = uid.get("filename") or uid.get("name")
            elif isinstance(uid, str):
                fname = os.path.basename(uid.rstrip("/").split("/")[-1])
            if not fname:
                continue
            # 防止路径穿越
            fname = os.path.basename(fname)
            src = os.path.join(src_root, fname)
            if not os.path.exists(src):
                missing.append(fname)
                continue
            shutil.copy2(src, os.path.join(workspace_path, fname))
            copied += 1
        msg = f"已复制 {copied} 个文件到工作区"
        if missing:
            msg += f"，缺失: {', '.join(missing)}"
        return {"success": True, "copied_count": copied, "message": msg}
    except Exception as e:
        return {"success": False, "copied_count": 0, "message": f"复制上传文件失败: {e}"}


@searchRouter.post("/upload")
async def upload_files(files: List[UploadFile] = File(...), subject: str = Form("自动分类")):
    """文件上传接口：multipart/form-data，字段名 files（可多个），保存到 upload_files/。
    返回与前端 displayUploadedFiles 兼容的结构 [{filename, size_mb, url}]。
    """
    try:
        upload_root = _upload_root()
        os.makedirs(upload_root, exist_ok=True)
        results = []
        for f in files:
            fname = _safe_upload_name(f.filename or "attachment")
            if not fname:
                fname = f"file_{uuid.uuid4().hex[:8]}"
            extension = Path(fname).suffix.lower()
            if extension == ".doc":
                raise HTTPException(415, "旧版 .doc 暂不支持，请另存为 .docx 或 PDF")
            if extension not in UPLOAD_EXTENSIONS:
                raise HTTPException(415, f"不支持的文件格式：{extension or '未知'}")
            # 同名文件加时间戳后缀避免覆盖
            dest = os.path.join(upload_root, fname)
            if os.path.exists(dest):
                stem, ext = os.path.splitext(fname)
                fname = f"{stem}_{uuid.uuid4().hex[:12]}{ext}"
                dest = os.path.join(upload_root, fname)
            size = 0
            with open(dest, "xb") as out:
                while chunk := await f.read(1024 * 1024):
                    out.write(chunk)
                    size += len(chunk)
                    if size > MAX_UPLOAD_BYTES:
                        out.close()
                        os.remove(dest)
                        raise HTTPException(413, "文件不能超过 50MB")
            # 普通上传与“我的资料”使用同一个真实文件，只建立一条资料索引，
            # 不再让前端把相同文件上传第二次。
            upload_id = str(uuid.uuid4())
            classified_subject = save_material(upload_id, fname, dest, size, subject)
            results.append({
                "upload_id": upload_id,
                "filename": fname,
                "size_mb": round(size / (1024 * 1024), 2),
                "subject": classified_subject,
                "url": f"{custom_config.get('base_api_url')}/upload_files/{fname}",
            })
        logger.info(f"上传完成: {results}")
        return {"code": 0, "message": "ok", "data": results}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"上传失败: {e}", exc_info=True)
        return {"code": 1, "message": f"上传失败: {e}", "data": []}


# ===== 学习资料库（队友新增：资料索引/分类/今日计划） =====
UPLOAD_EXTENSIONS = {".pdf", ".docx", ".pptx", ".txt", ".md", ".json", ".png", ".jpg", ".jpeg", ".gif"}
MAX_UPLOAD_BYTES = 50 * 1024 * 1024


@searchRouter.get("/deep-research/settings")
async def get_study_settings():
    """读取只保存在本机的联网/隐私偏好。"""
    return {"settings": get_preferences()}


@searchRouter.patch("/deep-research/settings")
async def patch_study_settings(params: Any = Body(...)):
    try:
        return {"settings": update_preferences(params or {})}
    except ValueError as error:
        raise HTTPException(400, str(error))


@searchRouter.get("/deep-research/study-schedule")
async def get_study_schedule():
    preferences = get_preferences()
    return {
        "subjects": study_db.get_subject_overview(),
        "study_time_slots": preferences["study_time_slots"],
        "default_daily_minutes": preferences["default_daily_minutes"],
    }


@searchRouter.patch("/deep-research/study-schedule")
async def patch_study_schedule(params: Any = Body(...)):
    try:
        params = params or {}
        slots = validate_study_time_slots(params.get("study_time_slots", []))
        submitted_subjects = params.get("subjects", [])
        before_subjects = {item["id"]: item["exam_date"] for item in study_db.get_subject_overview()}
        before_preferences = get_preferences()
        changed = slots != before_preferences["study_time_slots"] or any(
            before_subjects.get(int(item.get("id", -1))) != item.get("exam_date")
            for item in submitted_subjects if isinstance(item, dict)
        )
        study_db.update_subject_exam_dates(submitted_subjects)
        preferences = update_preferences({"study_time_slots": slots})
        replan_version, replan_message = None, "排期设置未变化，无需重排"
        if changed:
            try:
                replan_version, gaps, _ = scheduler.build_plan(
                    daily_minutes=preferences["default_daily_minutes"],
                    trigger="主页调整考试排期",
                    description="用户修改考试日期或每日复习时间段",
                    verbose=False,
                )
                replan_message = f"已自动生成计划 v{replan_version}"
                if gaps:
                    replan_message += f"，其中 {len(gaps)} 门课程存在时间缺口"
            except RuntimeError as error:
                replan_message = f"设置已保存，但暂未重排：{error}"
        return {
            "success": True,
            "subjects": study_db.get_subject_overview(),
            "study_time_slots": preferences["study_time_slots"],
            "default_daily_minutes": preferences["default_daily_minutes"],
            "replan_version": replan_version,
            "replan_message": replan_message,
        }
    except ValueError as error:
        raise HTTPException(400, str(error))


def _safe_upload_name(value: str) -> str:
    name = Path(value or "attachment").name
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip(" .")
    return name[:180] or "attachment"


def _upload_lib_root() -> Path:
    """学习资料库上传根目录（按 upload_id 子目录组织，供 study_library 索引）。"""
    from user_context import current_user
    if current_user.get():
        return Path(_upload_root()) / "library"
    configured = os.getenv("TRAFFIC_OPS_UPLOAD_DIR")
    return (Path(configured) if configured else Path.cwd()) / "upload_files" / "admin"


@searchRouter.post("/deep-research/upload")
async def upload_attachment(request: Request):
    """资料库上传（x-file-name / x-subject header 方式）→ 落盘 + study_library 索引。"""
    filename = _safe_upload_name(unquote(request.headers.get("x-file-name", "attachment")))
    extension = Path(filename).suffix.lower()
    if extension == ".doc":
        raise HTTPException(415, "旧版 .doc 暂不支持，请另存为 .docx 或 PDF")
    if extension not in UPLOAD_EXTENSIONS:
        raise HTTPException(415, f"不支持的文件格式：{extension or '未知'}")
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > MAX_UPLOAD_BYTES:
            raise HTTPException(413, "文件不能超过 50MB")
    if not body:
        raise HTTPException(400, "文件为空")
    if len(body) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, "文件不能超过 50MB")
    upload_id = str(uuid.uuid4())
    folder = _upload_lib_root() / upload_id
    folder.mkdir(parents=True, exist_ok=False)
    (folder / filename).write_bytes(body)
    subject = save_material(upload_id, filename, folder / filename, len(body),
                            unquote(request.headers.get("x-subject", "")))
    return {"upload_id": upload_id, "filename": filename, "subject": subject, "size": len(body),
            "size_mb": round(len(body) / 1024 / 1024, 2)}


@searchRouter.get("/deep-research/materials")
async def get_materials(subject: str = ""):
    """资料库列表（按科目筛选）；自动补录旧文件。"""
    index_existing_uploads(_upload_lib_root())
    return {"materials": list_materials(subject)}


@searchRouter.patch("/deep-research/materials/{upload_id}")
async def patch_material(upload_id: str, params: Any = Body(None)):
    """修改资料科目。"""
    subject = (params or {}).get("subject", "")
    if not subject:
        raise HTTPException(400, "科目不能为空")
    if not update_material_subject(upload_id, subject):
        raise HTTPException(404, "资料不存在")
    return {"success": True, "upload_id": upload_id, "subject": subject}


@searchRouter.get("/deep-research/today-plan")
async def today_plan(date: str = ""):
    """查询某日复习计划。

    只读取 study.db 中每门学科的最新计划版本。通用 DAG 的结果和工具调用
    日志存放在另一套数据中，不能混入学生的正式复习任务。
    """
    try:
        target = date_type.fromisoformat(date) if date else date_type.today()
    except ValueError:
        raise HTTPException(400, "日期格式应为 YYYY-MM-DD")

    conn = study_db.get_conn()
    try:
        rows = conn.execute(
            """
            WITH latest AS (
                SELECT subject_id, MAX(version) AS version
                FROM plans
                WHERE subject_id IS NOT NULL
                GROUP BY subject_id
            )
            SELECT p.task_type, p.est_minutes, p.status, p.resource, p.version,
                   COALESCE(s.name, '未分类科目') AS subject_name,
                   COALESCE(b.name, '综合训练') AS block_name
            FROM plans p
            JOIN latest l
              ON l.subject_id = p.subject_id AND l.version = p.version
            LEFT JOIN subjects s ON s.id = p.subject_id
            LEFT JOIN blocks b ON b.id = p.block_id
            WHERE p.date = ?
            ORDER BY s.name, p.id
            """,
            (target.isoformat(),),
        ).fetchall()
    finally:
        conn.close()

    tasks = []
    total_minutes = 0
    versions = set()
    for row in rows:
        minutes = max(0, int(row["est_minutes"] or 0))
        total_minutes += minutes
        versions.add(int(row["version"]))
        status = str(row["status"] or "待办")
        task = (
            f"[{status}] {row['subject_name']} · {row['block_name']}："
            f"{row['task_type']}（{minutes} 分钟）"
        )
        resource = str(row["resource"] or "").strip()
        if resource:
            task += f" · 资料：{resource}"
        tasks.append(task)

    return {
        "found": bool(tasks),
        "date": target.isoformat(),
        "title": "今日复习计划",
        "tasks": tasks,
        "total_minutes": total_minutes,
        "versions": sorted(versions),
    }


async def stream_fast_response(text: str, params: dict):
    """将快通道纯文本包装成 SSE 流式格式"""
    import json
    import asyncio

    response_data = {
        "contentType": "multi-modal",
        "content": [{"type": "text", "value": text}],
        "promptSentences": [],
        "sessionInfo": params.get("sessionInfo", {}),
        "code": 0,
        "message": "ok",
        "task": "chat",
        "changeType": "append"
    }

    yield json.dumps(response_data, ensure_ascii=False).encode('utf-8') + b'\n'
    await asyncio.sleep(0)

# 使用从环境变量获取的WORKSPACE_PATH（任务工作区根目录）
work_space_path = os.environ.get('WORKSPACE_PATH')
work_space_path = os.path.join(work_space_path, "work_space") if work_space_path else os.path.join(os.getcwd(), "work_space")
logger.info(f"Using work_space_path: {work_space_path}")
if not os.path.exists(work_space_path):
    os.makedirs(work_space_path)

# 确保logs目录存在（按 plan_id 记录规划日志）
LOGS_PATH = os.path.join(work_space_path, 'plans')
if not os.path.exists(LOGS_PATH):
    os.makedirs(LOGS_PATH)

# 为回放文件单独创建目录，避免与正常任务工作区混在一起
REPLAY_BASE_PATH = os.path.join(work_space_path, 'replay_history')
if not os.path.exists(REPLAY_BASE_PATH):
    os.makedirs(REPLAY_BASE_PATH)
logger.info(f"Using REPLAY_BASE_PATH: {REPLAY_BASE_PATH}")

# 回放间隔时长配置（秒），可通过环境变量 REPLAY_DELAY 设置，默认 0.3 秒
REPLAY_DELAY = float(os.environ.get("REPLAY_DELAY", "0.3"))


# 将本地文件路径转换为可被前端访问的URL
def _file_path_to_url(path_value: str) -> str:
    try:
        if not isinstance(path_value, str) or len(path_value) == 0:
            return path_value

        normalized = path_value.replace("\\", "/")
        marker = "work_space/"
        idx = normalized.find(marker)
        if idx == -1:
            return path_value

        relative = normalized[idx:]
        try:
            from cosight_server.sdk.common.config import custom_config
            base_url = str(custom_config.get("base_api_url"))
        except Exception:
            base_url = "/api/nae-deep-research/v1"

        parts = relative.split("/")
        if len(parts) >= 2:
            relative = "/".join(parts)

        return f"{base_url}/{relative}"
    except Exception:
        return path_value


def _rewrite_paths_in_payload(payload):
    try:
        if isinstance(payload, dict):
            new_obj = {}
            for k, v in payload.items():
                if k == "file_path" and isinstance(v, str):
                    new_obj[k] = _file_path_to_url(v)
                else:
                    new_obj[k] = _rewrite_paths_in_payload(v)
            return new_obj
        elif isinstance(payload, list):
            return [_rewrite_paths_in_payload(item) for item in payload]
        elif isinstance(payload, str):
            try:
                obj = json.loads(payload)
                return json.dumps(_rewrite_paths_in_payload(obj), ensure_ascii=False)
            except Exception:
                return _file_path_to_url(payload)
        else:
            return payload
    except Exception:
        return payload

async def _trigger_credibility_analysis(plan_queue, plan_data: Plan, completed_step: str):
    await _check_and_continue_next_step(plan_queue, plan_data)
    try:
        logger.info(f"准备创建可信分析任务: {completed_step}")
        task = asyncio.create_task(_async_credibility_analysis(plan_queue, plan_data, completed_step))
        logger.info(f"可信分析任务已创建: {completed_step}")
    except Exception as e:
        logger.error(f"创建可信分析任务失败: {e}", exc_info=True)

async def _async_credibility_analysis(plan_queue, plan_data: Plan, completed_step: str):
    try:
        logger.info(f"开始异步可信分析: {completed_step}")

        current_step = {
            "title": completed_step,
            "content": plan_data.step_details.get(completed_step, ""),
            "status": "completed"
        }

        all_completed_steps = []
        for step, status in plan_data.step_statuses.items():
            if status == 'completed':
                all_completed_steps.append({
                    "title": step,
                    "content": plan_data.step_details.get(step, ""),
                    "status": status
                })

        tool_events = []
        if hasattr(plan_data, 'step_tool_calls'):
            for step, tool_calls in plan_data.step_tool_calls.items():
                if step == completed_step:
                    for tool_call in tool_calls:
                        tool_events.append({
                            "tool_name": tool_call.get("tool_name", ""),
                            "tool_args": tool_call.get("tool_args", ""),
                            "timestamp": tool_call.get("timestamp", "")
                        })

        credibility_result = await credibility_analyzer.analyze_step_credibility(
            current_step, all_completed_steps, tool_events
        )

        if credibility_result:
            try:
                step_index = list(plan_data.steps).index(completed_step) if hasattr(plan_data, 'steps') else None
            except ValueError:
                step_index = None
            credibility_message = credibility_analyzer.format_credibility_message(
                credibility_result, completed_step, step_index
            )

            if plan_queue is not None:
                await plan_queue.put(credibility_message)
                try:
                    import json as _json
                    payload_len = len(_json.dumps(credibility_message, ensure_ascii=False))
                except Exception:
                    payload_len = -1
                logger.info(f"异步可信分析完成，已推送到队列 step={completed_step}, bytes~={payload_len}")
        else:
            logger.info(f"异步可信分析无结果: {completed_step}")

    except Exception as e:
        logger.error(f"异步可信分析失败: {e}", exc_info=True)

async def _check_and_continue_next_step(plan_queue, plan_data: Plan):
    try:
        logger.info(f"检查下一步骤，当前计划状态: {plan_data.step_statuses}")
        ready_steps = plan_data.get_ready_steps()
        logger.info(f"找到可执行的步骤: {ready_steps}")
        if ready_steps:
            logger.info(f"发现 {len(ready_steps)} 个可执行步骤，但等待主循环自然处理")
        else:
            logger.info("没有找到可执行的步骤，计划可能已完成或阻塞")
    except Exception as e:
        logger.error(f"检查下一步骤失败: {e}", exc_info=True)

async def append_create_plan(data: Any):
    try:
        file_path = Path(LOGS_PATH) / "plan.log"

        if isinstance(data, Plan):
            plan_dict = {
                "title": data.title if hasattr(data, 'title') else "",
                "steps": data.steps if hasattr(data, 'steps') else [],
                "step_files": data.step_files if hasattr(data, 'step_files') else {},
                "step_statuses": data.step_statuses if hasattr(data, 'step_statuses') else {},
                "step_notes": data.step_notes if hasattr(data, 'step_notes') else {},
                "step_details": data.step_details if hasattr(data, 'step_details') else {},
                "step_tool_calls": data.step_tool_calls if hasattr(data, 'step_tool_calls') else {},
                "dependencies": {str(k): v for k, v in data.dependencies.items()} if hasattr(data,
                                                                                             'dependencies') else {},
                "progress": data.get_progress() if hasattr(data, 'get_progress') and callable(
                    data.get_progress) else {},
                "result": data.get_plan_result() if hasattr(data, 'get_plan_result') and callable(
                    data.get_plan_result) else ""
            }
            logger.info(f"step_files:{data.step_files}")
            data = plan_dict
        elif isinstance(data, dict) and data.get("event_type") in ["tool_start", "tool_complete", "tool_error"]:
            data = _rewrite_paths_in_payload(data)
            logger.info(f"Tool event: {data.get('event_type')} for {data.get('tool_name')}")

        if isinstance(data, (dict, list)):
            try:
                content = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
            except TypeError as e:
                logger.error(f"JSON序列化失败，尝试转换对象: {e}", exc_info=True)
                if isinstance(data, dict):
                    serializable_data = {k: str(v) for k, v in data.items()}
                elif isinstance(data, list):
                    serializable_data = [
                        str(item) if not isinstance(item, (dict, list, str, int, float, bool, type(None))) else item for
                        item in data]
                content = json.dumps(serializable_data, ensure_ascii=False, indent=2) + "\n"
        else:
            content = str(data) + "\n"

        with open(file_path, mode='a', encoding='utf-8') as f:
            f.write(content)

        global plan_queue, main_loop, analyzed_steps
        if plan_queue is not None and main_loop is not None:
            if isinstance(data, Plan):
                asyncio.run_coroutine_threadsafe(plan_queue.put(plan_dict), main_loop)

                if hasattr(data, 'step_statuses'):
                    logger.info(f"Plan步骤状态: {data.step_statuses}")
                    for step, status in data.step_statuses.items():
                        logger.info(f"检查步骤: {step}, 状态: {status}, 已分析: {step in analyzed_steps}")
                        if status == 'completed' and step not in analyzed_steps:
                            analyzed_steps.add(step)
                            try:
                                asyncio.run_coroutine_threadsafe(_trigger_credibility_analysis(plan_queue, data, step), main_loop)
                            except Exception as e:
                                logger.error(f"触发可信分析失败: {e}", exc_info=True)
                            logger.info(f"触发可信分析: {step}")
            else:
                asyncio.run_coroutine_threadsafe(plan_queue.put(data), main_loop)

    except json.JSONDecodeError as e:
        logger.error(f"JSON序列化失败: {e}", exc_info=True)
    except IOError as e:
        logger.error(f"文件写入失败: {e}", exc_info=True)
    except Exception as e:
        logger.error(f"未知错误: {e}", exc_info=True)


def _clean_replay_json(json_text: str) -> str:
    if not json_text or not json_text.strip():
        return json_text

    try:
        data = json.loads(json_text.strip())
        fields_to_remove = ['save_mode', 'saveMode']

        def remove_fields(obj):
            if isinstance(obj, dict):
                cleaned = {}
                for key, value in obj.items():
                    if key not in fields_to_remove:
                        cleaned[key] = remove_fields(value)
                return cleaned
            elif isinstance(obj, list):
                return [remove_fields(item) for item in obj]
            else:
                return obj

        cleaned_data = remove_fields(data)
        return json.dumps(cleaned_data, ensure_ascii=False) + '\n'
    except (json.JSONDecodeError, Exception) as e:
        logger.warning(f"清理 replay.json 时出错，保留原始内容: {e}")
        return json_text


def validate_search_input(params: dict) -> dict | None:
    if not (content := params.get('content')) or len(content) == 0:
        return {
            "contentType": "multi-modal",
            "content": [{"type": "text", "value": i18n.t('invalid_command')}],
            "promptSentences": []
        }
    return None


@searchRouter.post("/deep-research/search")
async def search(request: Request, params: Any = Body(None)):
    from user_context import current_user
    if current_user.get():
        from cosight_server.deep_research.services.study_gateway import answer
        reply = await answer(params)
        return StreamingResponse(stream_fast_response(str(reply), params), media_type="application/json")
    raise HTTPException(401, "请先登录")

    if result := validate_search_input(params):
        return result
    session_id = params.get("sessionInfo", {}).get("sessionId", "")
    content_array = params.get('content', [])
    query_content = content_array[0]['value'] if content_array and isinstance(
        content_array, list) and len(content_array) > 0 and 'value' in content_array[0] else ""
    
    if session_id and query_content:
        try:
            from study_fast_channel import _add_question
            _add_question(session_id, query_content)
            logger.info(f"已记录用户消息到缓存: {query_content[:30]}...")
        except Exception as e:
            logger.warning(f"记录会话历史失败: {e}")
    is_replay_request = False
    try:
        if isinstance(params, dict):
            is_replay_request = bool(params.get("replay", False))
    except Exception:
        is_replay_request = False

    session_info = params.get("sessionInfo", {})
    plan_id = session_info.get("messageSerialNumber", "")
    if not plan_id:
        plan_id = f"plan_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}"

    
    # ===== 复习智能体快通道 =====
    if not is_replay_request:
        try:
            from study_fast_channel import try_fast_channel
        
            session_id = params.get("sessionInfo", {}).get("sessionId", "")
            history = params.get("history", [])
            uploaded_files = params.get("uploadedFiles", [])
            _fast = try_fast_channel(query_content, history, session_id, uploaded_files)
            if _fast is not None:
                logger.info("命中复习快通道，走流式返回")
                return StreamingResponse(
                    stream_fast_response(_fast, params),
                    media_type="application/json"
                )
        except Exception as _fe:
                logger.error(f"快通道异常，回落完整流程: {_fe}", exc_info=True)
           
    # 规划每个 plan 的持久化文件
    plan_log_path = os.path.join(LOGS_PATH, f"{plan_id}.log")
    plan_final_path = os.path.join(LOGS_PATH, f"{plan_id}.final.json")

    if not is_replay_request:
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S_%f')
        work_space_path_time = os.path.join(work_space_path, f'work_space_{timestamp}')
        print(f"work_space_path_time:{work_space_path_time}")
        os.makedirs(work_space_path_time, exist_ok=True)
        os.environ['WORKSPACE_PATH'] = work_space_path_time
    else:
        work_space_path_time = None

    uploaded_files = params.get('uploadedFiles', [])
    if uploaded_files:
        logger.info(f"Copying uploaded files to workspace: {uploaded_files}")
        try:
            copy_result = copy_uploaded_files_to_workspace(
                upload_ids=uploaded_files,
                workspace_path=work_space_path_time
            )
            if copy_result['success']:
                logger.info(f"Successfully copied {copy_result['copied_count']} file(s) to workspace")
            else:
                logger.warning(f"File copy completed with errors: {copy_result['message']}")
        except Exception as e:
            logger.error(f"Error copying uploaded files to workspace: {str(e)}", exc_info=True)

    async def generator_func():
        plan_queue = asyncio.Queue()
        main_loop = asyncio.get_running_loop()

        latest_plan = None
        analyzed_steps_local = set()

        def append_create_plan_local(data: Any):
            try:
                file_path = Path(plan_log_path)

                if isinstance(data, Plan):
                    plan_obj = data
                    plan_dict = {
                        "title": plan_obj.title if hasattr(plan_obj, 'title') else "",
                        "steps": plan_obj.steps if hasattr(plan_obj, 'steps') else [],
                        "step_files": plan_obj.step_files if hasattr(plan_obj, 'step_files') else {},
                        "step_statuses": plan_obj.step_statuses if hasattr(plan_obj, 'step_statuses') else {},
                        "step_notes": plan_obj.step_notes if hasattr(plan_obj, 'step_notes') else {},
                        "step_details": plan_obj.step_details if hasattr(plan_obj, 'step_details') else {},
                        "step_tool_calls": plan_obj.step_tool_calls if hasattr(plan_obj, 'step_tool_calls') else {},
                        "dependencies": {str(k): v for k, v in plan_obj.dependencies.items()} if hasattr(plan_obj,
                                                                                                     'dependencies') else {},
                        "progress": plan_obj.get_progress() if hasattr(plan_obj, 'get_progress') and callable(
                            data.get_progress) else {},
                        "result": plan_obj.get_plan_result() if hasattr(plan_obj, 'get_plan_result') and callable(
                            data.get_plan_result) else ""
                    }
                    logger.info(f"step_files:{plan_obj.step_files}")
                    data = plan_dict

                    if plan_queue is not None and main_loop is not None:
                        logger.info(f"Pushing Plan data to queue for plan_id: {plan_id}")
                        asyncio.run_coroutine_threadsafe(plan_queue.put(plan_dict), main_loop)

                    try:
                        if hasattr(plan_obj, 'step_statuses'):
                            for step, status in plan_obj.step_statuses.items():
                                if status == 'completed' and step not in analyzed_steps_local:
                                    analyzed_steps_local.add(step)
                                    try:
                                        asyncio.run_coroutine_threadsafe(_trigger_credibility_analysis(plan_queue, plan_obj, step), main_loop)
                                    except Exception as e:
                                        logger.error(f"触发可信分析失败: {e}", exc_info=True)
                                    logger.info(f"触发可信分析(本地队列): {step}")
                    except Exception as _e:
                        logger.error(f"触发可信分析失败: {_e}", exc_info=True)

                elif isinstance(data, dict) and data.get("event_type") in ["tool_start", "tool_complete", "tool_error"]:
                    try:
                        data = _rewrite_paths_in_payload(data)
                    except Exception:
                        pass
                    logger.info(f"Tool event received: {data.get('event_type')} for {data.get('tool_name')} at step {data.get('step_index')}")

                if isinstance(data, (dict, list)):
                    try:
                        content = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
                    except TypeError as e:
                        logger.error(f"JSON序列化失败，尝试转换对象: {e}", exc_info=True)
                        if isinstance(data, dict):
                            serializable_data = {k: str(v) for k, v in data.items()}
                        elif isinstance(data, list):
                            serializable_data = [str(item) if not isinstance(item, (
                            dict, list, str, int, float, bool, type(None))) else item for item in data]
                        content = json.dumps(serializable_data, ensure_ascii=False, indent=2) + "\n"
                else:
                    content = str(data) + "\n"

                with open(file_path, mode='a', encoding='utf-8') as f:
                    f.write(content)

                try:
                    if isinstance(data, dict) and data.get("result"):
                        with open(plan_final_path, mode='w', encoding='utf-8') as ff:
                            ff.write(json.dumps(data, ensure_ascii=False, indent=2))
                        try:
                            save_study_plan(plan_id, data)
                        except Exception as _se:
                            logger.warning(f"保存学习计划到资料库失败: {_se}")
                except Exception as _:
                    pass

                if plan_queue is not None and main_loop is not None:
                    if not isinstance(data, Plan):
                        safe_data = _rewrite_paths_in_payload(data)
                        logger.info(f"Pushing non-Plan data to queue: {type(data).__name__} for plan_id: {plan_id}")
                        asyncio.run_coroutine_threadsafe(plan_queue.put(safe_data), main_loop)
                else:
                    logger.warning(f"Queue or main_loop is None, cannot push data for plan_id: {plan_id}")

            except json.JSONDecodeError as e:
                logger.error(f"JSON序列化失败: {e}", exc_info=True)
            except IOError as e:
                logger.error(f"文件写入失败: {e}", exc_info=True)
            except Exception as e:
                logger.error(f"未知错误: {e}", exc_info=True)

        try:
            if not TaskManager.is_running(plan_id) and os.path.exists(plan_final_path):
                with open(plan_final_path, 'r', encoding='utf-8') as rf:
                    final_obj = json.load(rf)
                final_obj = dict(final_obj)
                final_obj["status_text"] = "执行完成"
                yield {"plan": final_obj}
                return
            if not TaskManager.is_running(plan_id) and os.path.exists(plan_log_path):
                with open(plan_log_path, 'r', encoding='utf-8') as lf:
                    for line in lf:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            obj = json.loads(line)
                        except Exception:
                            continue
                        if isinstance(obj, dict) and obj.get("event_type") in ["tool_start", "tool_complete", "tool_error"]:
                            yield {"plan": obj}
                        else:
                            yield {"plan": obj}
                return
        except Exception as _:
            pass

        def run_manus():
            try:
                os.environ['WORKSPACE_PATH'] = work_space_path_time

                logger.info(f"Subscribing to events for plan_id: {plan_id}")
                plan_report_event_manager.subscribe("plan_created", plan_id, append_create_plan_local)
                plan_report_event_manager.subscribe("plan_updated", plan_id, append_create_plan_local)
                plan_report_event_manager.subscribe("plan_process", plan_id, append_create_plan_local)
                plan_report_event_manager.subscribe("plan_result", plan_id, append_create_plan_local)
                plan_report_event_manager.subscribe("tool_event", plan_id, append_create_plan_local)
                logger.info(f"Event subscription completed for plan_id: {plan_id}")

                logger.info(f"llm is {llm_for_plan.model}, {llm_for_plan.base_url}, {llm_for_plan.api_key}")
                cosight = CoSight(
                    llm_for_plan,
                    llm_for_act,
                    llm_for_tool,
                    llm_for_vision,
                    work_space_path=work_space_path_time,
                    message_uuid = plan_id
                )
                result = cosight.execute(query_content)
                logger.info(f"final result is {result}")

            except Exception as e:
                logger.error(f"CoSight执行错误: {e}", exc_info=True)
            finally:
                plan_report_event_manager.unsubscribe("plan_created", plan_id, append_create_plan_local)
                plan_report_event_manager.unsubscribe("plan_updated", plan_id, append_create_plan_local)
                plan_report_event_manager.unsubscribe("plan_process", plan_id, append_create_plan_local)
                plan_report_event_manager.unsubscribe("plan_result", plan_id, append_create_plan_local)
                plan_report_event_manager.unsubscribe("tool_event", plan_id, append_create_plan_local)
                TaskManager.mark_completed(plan_id)
                TaskManager.remove_plan(plan_id)

        if TaskManager.is_running(plan_id):
            logger.info(f"Task already running for plan_id: {plan_id}, subscribing to events")
            plan_report_event_manager.subscribe("plan_created", plan_id, append_create_plan_local)
            plan_report_event_manager.subscribe("plan_updated", plan_id, append_create_plan_local)
            plan_report_event_manager.subscribe("plan_process", plan_id, append_create_plan_local)
            plan_report_event_manager.subscribe("plan_result", plan_id, append_create_plan_local)
            plan_report_event_manager.subscribe("tool_event", plan_id, append_create_plan_local)
        else:
            TaskManager.mark_running(plan_id)
            logger.info(f"Starting new task for plan_id: {plan_id}")
            import threading
            thread = threading.Thread(target=run_manus)
            thread.daemon = True
            thread.start()

        last_plan_fingerprint = None
        emitted_credibility_keys = set()
        plan_completed = False
        plan_completed_time = None
        while True:
            try:
                timeout = 5.0 if plan_completed else 60.0
                data = await asyncio.wait_for(plan_queue.get(), timeout=timeout)

                if isinstance(data, dict) and data.get("type") in ("credibility-analysis", "lui-message-credibility-analysis"):
                    try:
                        cred_key = f"{data.get('type')}|{data.get('stepTitle')}|{data.get('stepIndex')}"
                    except Exception:
                        cred_key = None
                    if cred_key is None or cred_key not in emitted_credibility_keys:
                        if cred_key is not None:
                            emitted_credibility_keys.add(cred_key)
                        yield data
                    continue

                if (
                    isinstance(data, dict)
                    and isinstance(data.get("plan"), dict)
                    and data["plan"].get("type") in ("credibility-analysis", "lui-message-credibility-analysis")
                ):
                    yield data["plan"]
                    continue

                if isinstance(data, dict) and data.get("event_type") in ["tool_start", "tool_complete", "tool_error"]:
                    yield data
                    continue

                if isinstance(data, dict) and "result" in data and data['result']:
                    latest_plan = data
                    completed_plan = dict(latest_plan)
                    completed_plan["statusText"] = "执行完成"
                    yield {"plan": completed_plan}
                    import time as _time
                    plan_completed = True
                    plan_completed_time = plan_completed_time or _time.monotonic()
                    continue

                latest_plan = data
                running_plan = dict(latest_plan) if isinstance(latest_plan, dict) else latest_plan
                if isinstance(running_plan, dict):
                    running_plan["statusText"] = "正在执行中"
                try:
                    import hashlib as _hashlib
                    plan_fp = _hashlib.md5(json.dumps(running_plan, ensure_ascii=False, sort_keys=True).encode('utf-8')).hexdigest() if isinstance(running_plan, dict) else None
                except Exception:
                    plan_fp = None
                if plan_fp is None or plan_fp != last_plan_fingerprint:
                    last_plan_fingerprint = plan_fp
                    yield {"plan": running_plan}

            except asyncio.TimeoutError:
                if plan_completed:
                    import time as _time
                    TAIL_WINDOW_SECONDS = 180.0
                    plan_completed_time = plan_completed_time or _time.monotonic()
                    elapsed = _time.monotonic() - plan_completed_time
                    if elapsed >= TAIL_WINDOW_SECONDS:
                        break
                    continue

                if latest_plan and isinstance(latest_plan, dict):
                    waiting_plan = dict(latest_plan)
                    waiting_plan["statusText"] = "等待计划更新..."
                    try:
                        import hashlib as _hashlib
                        plan_fp = _hashlib.md5(json.dumps(waiting_plan, ensure_ascii=False, sort_keys=True).encode('utf-8')).hexdigest()
                    except Exception:
                        plan_fp = None
                    if plan_fp is None or plan_fp != last_plan_fingerprint:
                        last_plan_fingerprint = plan_fp
                        yield {"plan": waiting_plan}
                else:
                    contains_chinese = any('\u4e00' <= c <= '\u9fff' for c in query_content)
                    title = "等待任务执行" if contains_chinese else "Waiting for task execution"
                    default_plan = {"title": title, "statusText": "等待计划更新...", "steps": []}
                    try:
                        import hashlib as _hashlib
                        plan_fp = _hashlib.md5(json.dumps(default_plan, ensure_ascii=False, sort_keys=True).encode('utf-8')).hexdigest()
                    except Exception:
                        plan_fp = None
                    if plan_fp is None or plan_fp != last_plan_fingerprint:
                        last_plan_fingerprint = plan_fp
                        yield {"plan": default_plan}
            except Exception as e:
                logger.error(f"生成响应错误: {e}", exc_info=True)
                if latest_plan and isinstance(latest_plan, dict):
                    error_plan = dict(latest_plan)
                    error_plan["statusText"] = f"生成响应出错: {str(e)}"
                    yield {"plan": error_plan}
                else:
                    yield {"plan": {"title": "任务出错", "statusText": f"生成响应出错: {str(e)}", "steps": []}}
                break

    async def generate_stream_response(generator_func, params):
        try:
            async for response_data in generator_func():
                if isinstance(response_data, dict) and response_data.get("type") in ("credibility-analysis", "lui-message-credibility-analysis"):
                    try:
                        logger.info("发送可信分析消息到前端")
                    except Exception:
                        pass
                    response_json = {
                        "contentType": "lui-message-credibility-analysis",
                        "sessionInfo": params.get("sessionInfo", {}),
                        "code": 0,
                        "message": "ok",
                        "task": "credibility_analysis",
                        "changeType": "append",
                        "content": response_data
                    }
                elif (
                    isinstance(response_data, dict)
                    and response_data.get("event_type") in ["tool_start", "tool_complete", "tool_error"]
                ):
                    try:
                        logger.info("发送工具事件到前端")
                    except Exception:
                        pass
                    response_json = {
                        "contentType": "lui-message-tool-event",
                        "sessionInfo": params.get("sessionInfo", {}),
                        "code": 0,
                        "message": "ok",
                        "task": "tool_event",
                        "changeType": "append",
                        "content": response_data
                    }
                elif (
                    isinstance(response_data, dict)
                    and isinstance(response_data.get("plan"), dict)
                    and response_data["plan"].get("type") in ("credibility-analysis", "lui-message-credibility-analysis")
                ):
                    try:
                        logger.info("发送plan内可信分析消息到前端")
                    except Exception:
                        pass
                    response_json = {
                        "contentType": "lui-message-credibility-analysis",
                        "sessionInfo": params.get("sessionInfo", {}),
                        "code": 0,
                        "message": "ok",
                        "task": "credibility_analysis",
                        "changeType": "append",
                        "content": response_data["plan"]
                    }
                elif isinstance(response_data, dict) and "plan" in response_data:
                    try:
                        logger.info("发送计划进度到前端")
                    except Exception:
                        pass
                    response_json = {
                        "contentType": "lui-message-manus-step",
                        "sessionInfo": params.get("sessionInfo", {}),
                        "code": 0,
                        "message": "ok",
                        "task": "chat",
                        "changeType": "replace",
                        "content": response_data["plan"]
                    }
                else:
                    try:
                        logger.info("发送兜底消息到前端")
                    except Exception:
                        pass
                    response_json = {
                        "contentType": "lui-message-manus-step",
                        "sessionInfo": params.get("sessionInfo", {}),
                        "code": 0,
                        "message": "ok",
                        "task": "chat",
                        "changeType": "replace",
                        "content": response_data
                    }

                yield json.dumps(response_json, ensure_ascii=False).encode('utf-8') + b'\n'
                await asyncio.sleep(0)

        except Exception as exc:
            error_msg = "生成回复时发生错误。"
            logger.exception(error_msg)
            error_response = json.dumps({
                "contentType": "lui-message-manus-step",
                "content": {"intro": error_msg, "steps": []},
                "sessionInfo": params.get("sessionInfo", {}),
                "code": 1,
                "message": "error",
                "task": "chat",
                "changeType": "replace"
            }, ensure_ascii=False).encode('utf-8') + b'\n'
            yield error_response

    async def RecordGenerator(workspace_path=None):
        try:
            replay_mode = bool(params.get("replay", False)) if isinstance(params, dict) else False
        except Exception:
            replay_mode = False

        explicit_workspace = None
        try:
            if isinstance(params, dict):
                explicit_workspace = params.get('replayWorkspace')
        except Exception:
            explicit_workspace = None

        if not replay_mode and not explicit_workspace:
            try:
                curr_workspace = os.environ.get('WORKSPACE_PATH')
            except Exception:
                curr_workspace = None

            if not curr_workspace and workspace_path:
                curr_workspace = workspace_path

            if not curr_workspace:
                timestamp = datetime.now().strftime('%Y%m%d_%H%M%S_%f')
                curr_workspace = os.path.join(work_space_path, f'work_space_{timestamp}')
        elif explicit_workspace and isinstance(explicit_workspace, str) and len(explicit_workspace) > 0:
            if not os.path.isabs(explicit_workspace):
                curr_workspace = os.path.join(os.getcwd(), explicit_workspace)
            else:
                curr_workspace = explicit_workspace
            logger.info(f"使用显式工作区路径: {curr_workspace}")
        else:
            try:
                curr_workspace = os.environ.get('WORKSPACE_PATH')
            except Exception:
                curr_workspace = None
            if not curr_workspace:
                curr_workspace = work_space_path

        replay_file_path = None
        if curr_workspace:
            try:
                workspace_name = os.path.basename(os.path.normpath(curr_workspace))
                if workspace_name:
                    replay_dir = os.path.join(REPLAY_BASE_PATH, workspace_name)
                    if not replay_mode:
                        os.makedirs(replay_dir, exist_ok=True)
                    replay_file_path = os.path.join(replay_dir, 'replay.json')
            except Exception as e:
                logger.error(f"处理回放目录失败: {e}")
                replay_file_path = None

        if replay_mode:
            try:
                if (not replay_file_path) or (replay_file_path and not os.path.exists(replay_file_path)):
                    try:
                        workspace_name = None
                        if curr_workspace:
                            workspace_name = os.path.basename(os.path.normpath(curr_workspace))
                        if workspace_name:
                            legacy_path = os.path.join(work_space_path, workspace_name, "replay.json")
                            if os.path.exists(legacy_path):
                                replay_file_path = legacy_path
                    except Exception as _e:
                        logger.warning(f"兼容旧回放目录失败: {_e}")

                if replay_file_path and not os.path.isabs(replay_file_path):
                    replay_file_path = os.path.join(os.getcwd(), replay_file_path)

                logger.info(f"========== 回放模式 ==========")
                logger.info(f"接收到的工作区路径: {explicit_workspace}")
                logger.info(f"解析后的工作区路径: {curr_workspace}")
                logger.info(f"回放文件完整路径: {replay_file_path}")
                logger.info(f"当前工作目录: {os.getcwd()}")
                logger.info(f"文件是否存在: {os.path.exists(replay_file_path) if replay_file_path else False}")

                if replay_file_path and os.path.exists(replay_file_path):
                    with open(replay_file_path, 'r', encoding='utf-8') as rf:
                        for line in rf:
                            line = line.rstrip('\n')
                            if not line:
                                await asyncio.sleep(REPLAY_DELAY)
                                continue
                            try:
                                yield line.encode('utf-8') + b'\n'
                            except Exception:
                                pass
                            await asyncio.sleep(REPLAY_DELAY)
                    return
                else:
                    fallback = {
                        "contentType": "lui-message-manus-step",
                        "sessionInfo": params.get("sessionInfo", {}) if isinstance(params, dict) else {},
                        "code": 0,
                        "message": "no replay file",
                        "task": "chat",
                        "changeType": "replace",
                        "content": {"title": "回放文件不存在", "steps": [], "statusText": "无可回放内容"}
                    }
                    yield json.dumps(fallback, ensure_ascii=False).encode('utf-8') + b'\n'
                    return
            except Exception as e:
                logger.error(f"回放模式失败: {e}", exc_info=True)
                replay_mode = False

        async for chunk in generate_stream_response(generator_func, params):
            try:
                if replay_file_path:
                    try:
                        text = chunk.decode('utf-8')
                    except Exception:
                        try:
                            text = str(chunk)
                        except Exception:
                            text = ''
                    if text:
                        cleaned_text = _clean_replay_json(text)
                        with open(replay_file_path, 'a', encoding='utf-8') as wf:
                            if cleaned_text.endswith('\n'):
                                wf.write(cleaned_text)
                            else:
                                wf.write(cleaned_text + '\n')
            except Exception as _e:
                logger.error(f"写入回放文件失败: {_e}", exc_info=True)

            yield chunk

    return StreamingResponse(
        RecordGenerator(work_space_path_time),
        media_type="application/json"
    )


@searchRouter.get("/search-results")
async def show_search_results(request: Request, query: str = "", tool: str = "", timestamp: str = ""):
    from fastapi.responses import HTMLResponse
    import urllib.parse

    import html
    raw_query = urllib.parse.unquote(query) if query else "搜索结果"
    decoded_query = html.escape(raw_query)
    tool = html.escape(tool)

    html_content = f"""
    <!DOCTYPE html>
    <html lang="zh-CN">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>搜索结果 - {decoded_query}</title>
        <style>
            body {{
                font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
                margin: 0;
                padding: 20px;
                background-color: #f5f5f5;
                line-height: 1.6;
            }}
            .container {{
                max-width: 800px;
                margin: 0 auto;
                background: white;
                border-radius: 8px;
                box-shadow: 0 2px 10px rgba(0,0,0,0.1);
                overflow: hidden;
            }}
            .header {{
                background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                color: white;
                padding: 20px;
                text-align: center;
            }}
            .header h1 {{
                margin: 0;
                font-size: 24px;
                font-weight: 300;
            }}
            .search-info {{
                padding: 20px;
                border-bottom: 1px solid #eee;
            }}
            .search-query {{
                font-size: 18px;
                color: #333;
                margin-bottom: 10px;
            }}
            .search-tool {{
                color: #666;
                font-size: 14px;
            }}
            .content {{
                padding: 20px;
            }}
            .message {{
                text-align: center;
                color: #666;
                font-size: 16px;
                margin: 40px 0;
            }}
            .external-links {{
                margin-top: 30px;
            }}
            .external-links h3 {{
                color: #333;
                margin-bottom: 15px;
            }}
            .link-item {{
                background: #f8f9fa;
                border: 1px solid #e9ecef;
                border-radius: 6px;
                padding: 15px;
                margin-bottom: 10px;
                transition: all 0.2s ease;
            }}
            .link-item:hover {{
                background: #e9ecef;
                transform: translateY(-1px);
            }}
            .link-item a {{
                color: #007bff;
                text-decoration: none;
                font-weight: 500;
            }}
            .link-item a:hover {{
                text-decoration: underline;
            }}
            .link-description {{
                color: #666;
                font-size: 14px;
                margin-top: 5px;
            }}
            .footer {{
                background: #f8f9fa;
                padding: 15px 20px;
                text-align: center;
                color: #666;
                font-size: 12px;
                border-top: 1px solid #eee;
            }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <h1>🔍 搜索结果</h1>
            </div>

            <div class="search-info">
                <div class="search-query">搜索内容：{decoded_query}</div>
                <div class="search-tool">搜索工具：{tool if tool else '未知'}</div>
            </div>

            <div class="content">
                <div class="message">
                    <p>📋 搜索结果已生成，但由于安全限制，无法在此页面直接嵌入显示。</p>
                    <p>💡 您可以在新窗口中打开以下链接查看详细内容：</p>
                </div>

                <div class="external-links">
                    <h3>🔗 相关搜索链接</h3>
                    <div class="link-item">
                        <a href="https://www.baidu.com/s?wd={urllib.parse.quote(raw_query)}" target="_blank" rel="noopener noreferrer">
                            🔍 百度搜索：{decoded_query}
                        </a>
                        <div class="link-description">在百度中搜索相关内容</div>
                    </div>
                    <div class="link-item">
                        <a href="https://www.google.com/search?q={urllib.parse.quote(raw_query)}" target="_blank" rel="noopener noreferrer">
                            🌐 Google搜索：{decoded_query}
                        </a>
                        <div class="link-description">在Google中搜索相关内容</div>
                    </div>
                    <div class="link-item">
                        <a href="https://zh.wikipedia.org/wiki/Special:Search?search={urllib.parse.quote(raw_query)}" target="_blank" rel="noopener noreferrer">
                            📚 维基百科：{decoded_query}
                        </a>
                        <div class="link-description">在维基百科中搜索相关内容</div>
                    </div>
                </div>
            </div>

            <div class="footer">
                <p>Co-Sight 智能搜索系统 | 生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</p>
            </div>
        </div>
    </body>
    </html>
    """

    return HTMLResponse(content=html_content)


@searchRouter.get("/replay/workspaces")
async def get_replay_workspaces():
    from user_context import current_user
    if current_user.get():
        return {"code": 0, "message": "多人版请使用聊天历史", "data": []}
    workspaces = []
    seen: set[str] = set()

    def _collect_from_base(base_dir: str, is_legacy: bool = False) -> None:
        if not os.path.exists(base_dir):
            return
        for folder_name in sorted(os.listdir(base_dir), reverse=True):
            folder_path = os.path.join(base_dir, folder_name)
            replay_file_path = os.path.join(folder_path, "replay.json")

            if folder_name in seen:
                continue

            if os.path.isdir(folder_path) and os.path.exists(replay_file_path):
                title = "未命名任务"
                message_count = 0

                try:
                    with open(replay_file_path, 'r', encoding='utf-8') as f:
                        lines = f.readlines()
                        message_count = len(lines)

                        if lines:
                            first_line_data = json.loads(lines[0])
                            content = first_line_data.get('content', {})
                            if isinstance(content, dict):
                                title = content.get('title', '未命名任务')
                            elif isinstance(content, str):
                                try:
                                    content_obj = json.loads(content)
                                    title = content_obj.get('title', '未命名任务')
                                except Exception:
                                    pass
                except Exception as e:
                    logger.warning(f"读取replay文件失败: {replay_file_path}, 错误: {e}")
                    continue

                mtime = os.path.getmtime(replay_file_path)

                workspaces.append({
                    "workspace_name": folder_name,
                    "workspace_path": f"work_space/{folder_name}",
                    "title": title,
                    "created_time": datetime.fromtimestamp(mtime).isoformat(),
                    "message_count": message_count,
                    "replay_file": (
                        f"replay_history/{folder_name}/replay.json"
                        if not is_legacy
                        else f"work_space/{folder_name}/replay.json"
                    ),
                })
                seen.add(folder_name)

    _collect_from_base(REPLAY_BASE_PATH, is_legacy=False)
    _collect_from_base(work_space_path, is_legacy=True)

    return {
        "code": 0,
        "message": "success",
        "data": workspaces,
    }
