"""Optional HTTP Basic login for hosted deployments.

Set AVB_USERS="alice:secret1,bob:secret2" to require a login. When it is set,
the logged-in username is recorded as the actor and reviewer in the audit
trail, instead of the free-text name the UI sends. When it is unset (local
use on your own PC) the app is open and the free-text name is used.
"""

from __future__ import annotations

import base64
import binascii
import os
import secrets

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

OPEN_PATHS = {"/api/health"}  # lets the host's health check through


def load_users() -> dict[str, str]:
    users = {}
    for pair in os.environ.get("AVB_USERS", "").split(","):
        name, sep, pw = pair.strip().partition(":")
        if sep and name and pw:
            users[name] = pw
    return users


def _check(header: str | None, users: dict[str, str]) -> str | None:
    if not header or not header.startswith("Basic "):
        return None
    try:
        name, _, pw = base64.b64decode(header[6:]).decode().partition(":")
    except (binascii.Error, UnicodeDecodeError):
        return None
    expected = users.get(name)
    if expected is not None and secrets.compare_digest(pw.encode(), expected.encode()):
        return name
    return None


class BasicAuthMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, users: dict[str, str]):
        super().__init__(app)
        self.users = users

    async def dispatch(self, request: Request, call_next):
        if request.url.path in OPEN_PATHS:
            return await call_next(request)
        user = _check(request.headers.get("authorization"), self.users)
        if not user:
            return Response("Login required", status_code=401,
                            headers={"WWW-Authenticate": 'Basic realm="Agile-Validation Bridge"'})
        request.state.user = user
        return await call_next(request)


def current_user(request: Request) -> str | None:
    return getattr(request.state, "user", None)
