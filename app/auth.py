"""Optional login for hosted deployments.

Set AVB_USERS="alice:secret1,bob:secret2" to require a login. When it is set,
the logged-in username is recorded as the actor and reviewer in the audit
trail, instead of the free-text name the UI sends. When it is unset (local
use on your own PC) the app is open and the free-text name is used.

Browsers sign in on /login and get a signed session cookie. Scripts can still
send an HTTP Basic "Authorization" header. Unauthenticated requests never get
a WWW-Authenticate header, so browsers don't show their own login popup
(which many phones and in-app browsers don't show at all).
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import os
import secrets
import time

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse

OPEN_PATHS = {"/api/health", "/login", "/logout"}  # health check + the sign-in page itself
COOKIE = "avb_session"
SESSION_SECONDS = 12 * 60 * 60


def load_users() -> dict[str, str]:
    users = {}
    for pair in os.environ.get("AVB_USERS", "").split(","):
        name, sep, pw = pair.strip().partition(":")
        if sep and name and pw:
            users[name] = pw
    return users


def check_password(users: dict[str, str], name: str, pw: str) -> bool:
    expected = users.get(name)
    # compare against a dummy for unknown names so timing doesn't reveal them
    return secrets.compare_digest(pw.encode(), (expected or "\0invalid").encode()) and expected is not None


def _secret() -> bytes:
    # AVB_SECRET if set; otherwise derived from AVB_USERS, so changing any
    # password signs everyone out.
    base = os.environ.get("AVB_SECRET") or os.environ.get("AVB_USERS", "")
    return hashlib.sha256(b"avb-session-v1\0" + base.encode()).digest()


def make_session(user: str) -> str:
    payload = base64.urlsafe_b64encode(f"{user}|{int(time.time()) + SESSION_SECONDS}".encode()).decode()
    sig = hmac.new(_secret(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}.{sig}"


def read_session(value: str | None, users: dict[str, str]) -> str | None:
    if not value or "." not in value:
        return None
    payload, sig = value.rsplit(".", 1)
    good = hmac.new(_secret(), payload.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, good):
        return None
    try:
        user, _, exp = base64.urlsafe_b64decode(payload).decode().rpartition("|")
        if int(exp) < time.time():
            return None
    except (binascii.Error, UnicodeDecodeError, ValueError):
        return None
    return user if user in users else None


def _basic(header: str | None, users: dict[str, str]) -> str | None:
    if not header or not header.startswith("Basic "):
        return None
    try:
        name, _, pw = base64.b64decode(header[6:]).decode().partition(":")
    except (binascii.Error, UnicodeDecodeError):
        return None
    return name if check_password(users, name, pw) else None


class LoginMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, users: dict[str, str]):
        super().__init__(app)
        self.users = users

    async def dispatch(self, request: Request, call_next):
        if request.url.path in OPEN_PATHS:
            return await call_next(request)
        user = read_session(request.cookies.get(COOKIE), self.users) or _basic(
            request.headers.get("authorization"), self.users)
        if not user:
            if request.url.path.startswith("/api/"):
                return JSONResponse({"detail": "Login required"}, status_code=401)
            return RedirectResponse("/login", status_code=303)
        request.state.user = user
        return await call_next(request)


def current_user(request: Request) -> str | None:
    return getattr(request.state, "user", None)
