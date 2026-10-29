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

from typing import Optional

from fastapi import APIRouter, Header
from starlette.responses import Response

from app.common.logger_util import logger
from cosight_server.sdk.services.session_manager import session_manager

userRouter = APIRouter()


@userRouter.get("/deep-research/login")
async def login(
    response: Response,
    cookie: Optional[str] = Header(None),
    referer: Optional[str] = Header(None, alias="Referer")
):
    from user_context import current_user
    user = current_user.get()
    return {"code": 0, "data": {"id": user["id"], "authValue": user["id"], "name": user["username"]}}


@userRouter.post("/deep-research/logout")
def logout(response: Response, cookie: Optional[str] = Header(None)):
    logger.info(f"logout >>>>>>>>>> is called")
    from cosight_server.deep_research.services import account_service as accounts
    from http.cookies import SimpleCookie
    parsed = SimpleCookie(cookie or "")
    value = parsed.get(accounts.COOKIE_NAME)
    accounts.revoke_session(value.value if value else "")
    response.delete_cookie(accounts.COOKIE_NAME, path="/")
    return {"code": 0, "success": True}
