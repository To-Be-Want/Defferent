from fastapi import APIRouter, HTTPException
from user_context import scoped_session_id
from cosight_server.deep_research.services.study_gateway import history, conversations

conversationRouter = APIRouter(prefix="/deep-research")


@conversationRouter.get("/conversations")
def list_conversations():
    return {"conversations": conversations()}


@conversationRouter.get("/conversations/{session_id}")
def get_conversation(session_id: str):
    if len(session_id) > 160:
        raise HTTPException(400, "会话ID过长")
    return {"messages": history(scoped_session_id(session_id))}
