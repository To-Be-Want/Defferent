"""Pure ASGI middleware keeps identity alive through streaming and WebSocket lifetimes."""
import hmac
from urllib.parse import urlsplit
from starlette.requests import HTTPConnection
from starlette.responses import JSONResponse, RedirectResponse
from user_context import current_user
from cosight_server.deep_research.services import account_service as accounts


def same_origin(connection):
    origin = connection.headers.get("origin")
    if not origin:
        return connection.scope["type"] != "websocket"
    parsed = urlsplit(origin)
    return parsed.scheme in ("http", "https") and parsed.netloc.lower() == connection.headers.get("host", "").lower()


class AccountMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] not in ("http", "websocket"):
            return await self.app(scope, receive, send)
        connection = HTTPConnection(scope)
        path = scope["path"]
        public = path in ("/", "/cosight", "/auth/config", "/auth/login", "/auth/register", "/healthz") or path.startswith("/cosight/")
        if path in ("/docs", "/redoc", "/openapi.json"):
            return await JSONResponse({"detail": "Not found"}, 404)(scope, receive, send)
        if not same_origin(connection):
            if scope["type"] == "websocket":
                return await send({"type": "websocket.close", "code": 4403})
            return await JSONResponse({"detail": "请求来源不允许"}, 403)(scope, receive, send)
        user = accounts.read_session(connection.cookies.get(accounts.COOKIE_NAME))
        if not public and not user:
            if scope["type"] == "websocket":
                return await send({"type": "websocket.close", "code": 4401})
            if path.startswith("/quiz/"):
                from urllib.parse import quote
                response = RedirectResponse("/cosight/login.html?next=" + quote(path, safe=""), 303)
            else:
                response = JSONResponse({"detail": "请先登录"}, 401)
            return await response(scope, receive, send)
        if scope["type"] == "http" and scope["method"] not in ("GET", "HEAD", "OPTIONS") and path not in ("/auth/login", "/auth/register"):
            supplied = connection.headers.get("x-csrf-token", "")
            if not user or not hmac.compare_digest(supplied, user["csrf_token"]):
                return await JSONResponse({"detail": "安全校验失败，请刷新后重试"}, 403)(scope, receive, send)
        scope.setdefault("state", {})["user"] = user
        token = current_user.set(user)
        async def private_send(message):
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                headers.extend([(b"x-content-type-options", b"nosniff"),
                    (b"referrer-policy", b"same-origin"), (b"x-frame-options", b"DENY")])
                if user or path.startswith("/auth/"):
                    headers.append((b"cache-control", b"private, no-store"))
                message = {**message, "headers": headers}
            await send(message)
        try:
            await self.app(scope, receive, private_send)
        finally:
            current_user.reset(token)
