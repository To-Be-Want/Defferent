"""Scoped learning API, independent of the legacy unrestricted research engine."""
import json
import uuid
from datetime import date
from pathlib import Path
from urllib.parse import unquote, quote
from fastapi import APIRouter, BackgroundTasks, Body, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import StreamingResponse
import scheduler
import study_db
from study_preferences import get_preferences, update_preferences, validate_study_time_slots
from user_context import user_root
from cosight_server.deep_research.services.study_library import save_material, list_materials, update_material_subject

learningRouter = APIRouter()
EXTENSIONS = {".pdf", ".docx", ".pptx", ".txt", ".md", ".json", ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".tif", ".tiff", ".webp"}
MAX_FILE_BYTES = 500 * 1024 * 1024


def safe_name(value):
    import re
    name = Path(str(value).replace("\\", "/")).name
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip(" .")[:160]
    if not name or Path(name).suffix.lower() not in EXTENSIONS:
        raise HTTPException(415, "不支持的文件类型；请使用PDF、DOCX、PPTX、文本或常见图片")
    return name


def finish_upload(upload_id, name, destination, size, subject):
    classified = save_material(upload_id, name, destination, size, subject)
    return {"upload_id": upload_id, "filename": name, "size": size,
            "size_mb": round(size / 1024 / 1024, 2), "subject": classified,
            "url": f"/api/nae-deep-research/v1/deep-research/materials/{upload_id}/download"}


@learningRouter.post("/upload")
async def upload_files(files: list[UploadFile] = File(...), subject: str = Form("自动分类")):
    if not 1 <= len(files) <= 10:
        raise HTTPException(400, "一次可上传1—10个文件")
    results = []
    for file in files:
        name = safe_name(file.filename)
        upload_id = uuid.uuid4().hex
        folder = user_root() / "upload_files" / upload_id
        folder.mkdir(parents=True)
        destination = folder / name
        size = 0
        try:
            with destination.open("xb") as output:
                while chunk := await file.read(1024 * 1024):
                    size += len(chunk)
                    if size > MAX_FILE_BYTES:
                        raise HTTPException(413, "单个文件不能超过500MB")
                    output.write(chunk)
            if not size:
                raise HTTPException(400, "不能上传空文件")
            results.append(finish_upload(upload_id, name, destination, size, subject))
        except Exception:
            destination.unlink(missing_ok=True)  # Only this newly created incomplete file.
            raise
        finally:
            await file.close()
    return {"code": 0, "message": "ok", "data": results}


@learningRouter.post("/deep-research/upload")
async def upload_attachment(request: Request):
    name = safe_name(unquote(request.headers.get("x-file-name", "")))
    upload_id = uuid.uuid4().hex
    folder = user_root() / "upload_files" / upload_id
    folder.mkdir(parents=True)
    destination = folder / name
    size = 0
    try:
        with destination.open("xb") as output:
            async for chunk in request.stream():
                size += len(chunk)
                if size > MAX_FILE_BYTES:
                    raise HTTPException(413, "单个文件不能超过500MB")
                output.write(chunk)
        if not size:
            raise HTTPException(400, "不能上传空文件")
        return finish_upload(upload_id, name, destination, size, unquote(request.headers.get("x-subject", "")))
    except Exception:
        destination.unlink(missing_ok=True)
        raise


@learningRouter.get("/deep-research/materials")
def materials(subject: str = ""):
    from cosight_server.deep_research.services.material_parser import states
    from learning_agent.source_index import stats
    parsed = states()
    indexed = stats()
    return {"materials": [{**item, "parsing": parsed.get(item['upload_id']), 'source_index':indexed.get(item['upload_id'])}
                          for item in list_materials(subject)]}


@learningRouter.get('/deep-research/knowledge-blocks/{block_id}/sources')
def knowledge_sources(block_id: int):
    from learning_agent.source_index import lookup
    with study_db.get_conn() as conn:
        if not conn.execute("SELECT id FROM blocks WHERE id=? AND status NOT IN ('跳过','已归档')", (block_id,)).fetchone():
            raise HTTPException(404, '知识点不存在')
    return lookup(block_id=block_id)


@learningRouter.post('/deep-research/materials/{upload_id}/parse', status_code=202)
def parse_material(upload_id: str, background_tasks: BackgroundTasks, body: dict = Body(...)):
    from cosight_server.deep_research.services.material_parser import start
    if type(body.get('reparse', False)) is not bool:
        raise HTTPException(400, '重新解析确认格式不正确')
    return start(upload_id, body.get('kind'), body.get('reparse', False), background_tasks)


@learningRouter.patch("/deep-research/materials/{upload_id}")
def reclassify(upload_id: str, body: dict = Body(...)):
    from cosight_server.deep_research.services.material_parser import correct_classification
    return correct_classification(upload_id, body)


@learningRouter.get("/deep-research/settings")
def settings():
    return {"settings": get_preferences()}


@learningRouter.patch("/deep-research/settings")
def save_settings(body: dict = Body(...)):
    try:
        return {"settings": update_preferences(body)}
    except (ValueError, TypeError) as error:
        raise HTTPException(400, str(error))


@learningRouter.get("/deep-research/study-schedule")
def schedule():
    preferences = get_preferences()
    return {"subjects": study_db.get_subject_overview(),
            "study_time_slots": preferences["study_time_slots"],
            "default_daily_minutes": preferences["default_daily_minutes"]}


@learningRouter.patch("/deep-research/study-schedule")
def save_schedule(body: dict = Body(...)):
    try:
        slots = validate_study_time_slots(body.get("study_time_slots"))
        subjects = body.get("subjects", [])
        old = schedule()
        exams_changed = study_db.update_subject_exam_dates(subjects)
        preferences = update_preferences({"study_time_slots": slots})
        changed = slots != old["study_time_slots"] or exams_changed
        version, message = None, "排期未变化，无需重排"
        if changed:
            message = "设置已保存，现有复习计划未修改。请在聊天中预览调整，查看延误和时间缺口后确认。"
        return {"success": True, **schedule(), "replan_version": version, "replan_message": message}
    except (ValueError, TypeError, KeyError, AttributeError) as error:
        raise HTTPException(400, "排期格式不正确：" + str(error))


@learningRouter.get("/deep-research/today-plan")
def today_plan(date: str = ""):
    from cosight_server.deep_research.services.study_plans import day_plan
    try:
        return day_plan(date)
    except ValueError as error:
        raise HTTPException(400, str(error)) from None




@learningRouter.patch("/deep-research/tasks/{task_id}")
def task_status(task_id: int, body: dict = Body(...)):
    if body.get("status") not in ("完成", "待办"):
        raise HTTPException(400, "任务状态不正确")
    with study_db.get_conn() as conn:
        cursor = conn.execute("UPDATE plans SET status=? WHERE id=?", (body["status"], task_id))
        if not cursor.rowcount:
            raise HTTPException(404, "任务不存在")
    return {"success": True}


@learningRouter.post("/deep-research/search")
async def search(body: dict = Body(...)):
    from cosight_server.deep_research.services.study_gateway import answer
    events = []
    reply = await answer(body, on_event=events.append)
    payload = {"contentType": "multi-modal", "content": [{"type": "text", "value": str(reply)}],
               "sessionInfo": body.get("sessionInfo", {}), "code": 0, "changeType": "append"}
    async def stream():
        for event in events:
            if event.get("type") == "lui-message-manus-step":
                yield json.dumps({**event, "sessionInfo": body.get("sessionInfo", {}), "code": 0}, ensure_ascii=False) + "\n"
        yield json.dumps(payload, ensure_ascii=False) + "\n"
    return StreamingResponse(stream(), media_type="application/json")


@learningRouter.get("/replay/workspaces")
def replay_workspaces():
    return {"code": 0, "data": [], "message": "多人版请使用聊天历史"}
