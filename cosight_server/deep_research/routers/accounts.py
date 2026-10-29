import hmac
import os
from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool
from cosight_server.deep_research.services import account_service as accounts
from user_context import current_user

accountRouter = APIRouter(prefix="/auth")


def registration_mode():
    configured = os.getenv("COSIGHT_REGISTRATION_MODE", "").strip().lower()
    mode = configured or ("invite" if os.getenv("COSIGHT_REGISTRATION_CODE") else "open")
    return mode if mode in ("open", "invite", "closed") else "closed"


@accountRouter.get("/config")
def account_config():
    return {"registration_mode": registration_mode(), "password_min_length": 10}


class Credentials(BaseModel):
    username: str = Field(min_length=3, max_length=100)
    password: str = Field(min_length=10, max_length=128)
    invitation_code: str = Field(default="", max_length=256)


def rate_limit(request):
    client = request.client.host if request.client else "unknown"
    if not accounts.allow_attempt("auth:" + client):
        raise HTTPException(429, "尝试过于频繁，请15分钟后重试")


def issue_cookie(response, user):
    token, csrf = accounts.create_session(user["id"])
    secure = os.getenv("COSIGHT_COOKIE_SECURE", "true").lower() != "false"
    response.set_cookie(accounts.COOKIE_NAME, token, max_age=accounts.SESSION_SECONDS,
                        httponly=True, secure=secure, samesite="lax", path="/")
    return {"user": user, "csrf_token": csrf}


@accountRouter.post("/register")
async def register(body: Credentials, request: Request, response: Response):
    rate_limit(request)
    mode = registration_mode()
    invitation = os.getenv("COSIGHT_REGISTRATION_CODE", "")
    if mode == "closed":
        raise HTTPException(403, "当前未开放注册，请联系管理员创建账号")
    if mode == "invite" and (not invitation or not hmac.compare_digest(body.invitation_code.encode(), invitation.encode())):
        raise HTTPException(403, "邀请码无效")
    try:
        user = await run_in_threadpool(accounts.create_user, body.username, body.password)
    except ValueError as error:
        raise HTTPException(400, str(error))
    return issue_cookie(response, user)


@accountRouter.post("/login")
async def login(body: Credentials, request: Request, response: Response):
    rate_limit(request)
    user = await run_in_threadpool(accounts.authenticate, body.username, body.password)
    if not user:
        raise HTTPException(401, "账号或密码错误")
    old = request.cookies.get(accounts.COOKIE_NAME)
    if old:
        accounts.revoke_session(old)
    return issue_cookie(response, user)


@accountRouter.get("/me")
def me():
    user = current_user.get()
    return {"user": {"id": user["id"], "username": user["username"]}, "csrf_token": user["csrf_token"]}


@accountRouter.post("/logout")
def logout(request: Request, response: Response):
    accounts.revoke_session(request.cookies.get(accounts.COOKIE_NAME, ""))
    response.delete_cookie(accounts.COOKIE_NAME, path="/")
    return {"success": True}
