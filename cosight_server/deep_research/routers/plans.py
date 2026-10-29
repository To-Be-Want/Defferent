from fastapi import APIRouter, Body, HTTPException
from cosight_server.deep_research.services import study_plans

planRouter = APIRouter()


@planRouter.post('/deep-research/parse-tasks')
def parse_tasks(body: dict = Body(...)):
    text = body.get('text', '')
    if not isinstance(text, str) or not 1 <= len(text.strip()) <= 4000:
        raise HTTPException(400, '请输入1—4000字的事项描述')
    from cosight_server.deep_research.services.study_experience import parse_tasks
    try:
        return parse_tasks(text)
    except (ValueError, TypeError, KeyError) as error:
        raise HTTPException(400, str(error)) from None


@planRouter.get("/deep-research/plan-overview")
def overview(include_archived: bool = False):
    return study_plans.overview(include_archived)


@planRouter.post("/deep-research/manual-tasks", status_code=201)
def create(body: dict = Body(...)):
    try:
        return study_plans.create_task(body)
    except ValueError as error:
        raise HTTPException(400, str(error)) from None


@planRouter.patch("/deep-research/manual-tasks/{task_id}")
def update(task_id: int, body: dict = Body(...)):
    try:
        result = study_plans.update_task(task_id, body)
    except ValueError as error:
        raise HTTPException(400, str(error)) from None
    if result is None:
        raise HTTPException(404, "事项不存在")
    return result


@planRouter.delete("/deep-research/manual-tasks/{task_id}")
def remove(task_id: int):
    if not study_plans.delete_task(task_id):
        raise HTTPException(404, "事项不存在")
    return {"success": True}
