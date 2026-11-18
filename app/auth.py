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


def client_ip(request: Request) -> str:
    # Vercel and Render put the real client first in X-Forwarded-For.
    fwd = request.headers.get("x-forwarded-for", "")
    return fwd.split(",")[0].strip() or (request.client.host if request.client else "unknown")


class LoginLimiter:
    """Slows down password guessing. Failures live in the database because
    serverless instances don't share memory."""

    WINDOW = 15 * 60
    PER_USER = 5
    PER_IP = 30

    def __init__(self, conn):
        self.conn = conn

    def blocked(self, username: str, ip: str) -> bool:
        since = time.time() - self.WINDOW
        by_user = self.conn.execute(
            "SELECT COUNT(*) AS n FROM login_failures WHERE username = ? AND at > ?", (username, since)
        ).fetchone()["n"]
        by_ip = self.conn.execute(
            "SELECT COUNT(*) AS n FROM login_failures WHERE ip = ? AND at > ?", (ip, since)
        ).fetchone()["n"]
        return by_user >= self.PER_USER or by_ip >= self.PER_IP

    def failed(self, username: str, ip: str) -> None:
        now = time.time()
        self.conn.execute("INSERT INTO login_failures (at, username, ip) VALUES (?, ?, ?)",
                          (now, username[:200], ip[:100]))
        self.conn.execute("DELETE FROM login_failures WHERE at < ?", (now - 24 * 3600,))

    def succeeded(self, username: str) -> None:
        self.conn.execute("DELETE FROM login_failures WHERE username = ?", (username,))


def _basic_credentials(header: str | None) -> tuple[str, str] | None:
    if not header or not header.startswith("Basic "):
        return None
    try:
        name, _, pw = base64.b64decode(header[6:]).decode().partition(":")
    except (binascii.Error, UnicodeDecodeError):
        return None
    return name, pw


class LoginMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, users: dict[str, str], limiter: LoginLimiter):
        super().__init__(app)
        self.users = users
        self.limiter = limiter

    async def dispatch(self, request: Request, call_next):
        if request.url.path in OPEN_PATHS:
            return await call_next(request)
        user = read_session(request.cookies.get(COOKIE), self.users)
        creds = None if user else _basic_credentials(request.headers.get("authorization"))
        if creds:  # script login: same guessing limits as the sign-in page
            name, pw = creds
            ip = client_ip(request)
            if self.limiter.blocked(name, ip):
                return JSONResponse({"detail": "Too many failed sign-ins. Try again in 15 minutes."},
                                    status_code=429)
            if check_password(self.users, name, pw):
                user = name
            else:
                self.limiter.failed(name, ip)
        if not user:
            if request.url.path.startswith("/api/"):
                return JSONResponse({"detail": "Login required"}, status_code=401)
            return RedirectResponse("/login", status_code=303)
        request.state.user = user
        return await call_next(request)


def current_user(request: Request) -> str | None:
    return getattr(request.state, "user", None)
