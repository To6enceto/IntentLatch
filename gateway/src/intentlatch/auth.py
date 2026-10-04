import hmac
import time
from collections.abc import Awaitable, Callable

from fastapi import Request

from . import console_users, metrics, teams
from .console_users import ConsoleUser, Role
from .errors import GatewayError
from .identity import Identity, TokenError, bearer_token, match_identity, verify_token

TOKEN_MESSAGES = {
    "token_missing": "An employee token is required: send Authorization: Bearer <token>.",
    "token_invalid": "The employee token is not valid.",
    "token_revoked": "The employee token has been revoked or replaced.",
}
AUTH_FAILURE_REASONS = {"token_missing": "missing", "token_invalid": "invalid", "token_revoked": "revoked"}


def challenge(code: str) -> dict[str, str]:
    if code == "token_missing":
        return {"WWW-Authenticate": "Bearer"}
    return {"WWW-Authenticate": 'Bearer error="invalid_token"'}


# The console sends this header on every request. A cross-site page cannot add it
# without a CORS preflight, which the gateway never grants.
CONSOLE_HEADER = "x-intentlatch-console"
SAFE_METHODS = ("GET", "HEAD")


# Paths under /admin whose roles differ from the default (viewer reads, admin writes):
# analysts write test cases and runs, and reports hold prompt text, so they need analyst to read.
PATH_ROLES: tuple[tuple[str, Role, Role], ...] = (
    ("/admin/test-cases", "analyst", "viewer"),
    ("/admin/test-runs", "analyst", "viewer"),
    ("/admin/reports", "admin", "analyst"),
)


def required_role(method: str, write_role: Role = "admin", read_role: Role = "viewer", path: str = "") -> Role:
    for prefix, path_write, path_read in PATH_ROLES:
        if path == prefix or path.startswith(f"{prefix}/"):
            write_role, read_role = path_write, path_read
    return read_role if method in SAFE_METHODS else write_role


async def console_user(request: Request) -> ConsoleUser:
    token = request.cookies.get(console_users.SESSION_COOKIE)
    if not token:
        raise GatewayError(401, "session_missing", "Sign in to the console.")
    user = await console_users.load_session(request.app.state.pool, token)
    if user is None:
        raise GatewayError(401, "session_expired", "The console session has expired. Sign in again.")
    return user


def require_console_header(request: Request) -> None:
    if request.method not in SAFE_METHODS and CONSOLE_HEADER not in request.headers:
        raise GatewayError(403, "console_header_missing", f"Console requests must send {CONSOLE_HEADER}.")


async def require_console_user(request: Request) -> ConsoleUser:
    require_console_header(request)
    return await console_user(request)


def admin_access(write_role: Role = "admin", read_role: Role = "viewer") -> Callable[[Request], Awaitable[None]]:
    """Admits the admin API key, or a console session whose role allows the method."""

    async def check(request: Request) -> None:
        header = request.headers.get("authorization")
        if header is None and console_users.SESSION_COOKIE in request.cookies:
            user = await require_console_user(request)
            required = required_role(request.method, write_role, read_role, request.url.path)
            if not console_users.allows(user.role, required):
                raise GatewayError(403, "role_forbidden", f"This action requires the {required} role.")
            request.state.console_user = user
            return
        expected = request.app.state.settings.admin_api_key.get_secret_value().encode()
        try:
            supplied = bearer_token(header).encode()
        except TokenError:
            supplied = b""
        if not hmac.compare_digest(supplied, expected):
            metrics.AUTH_FAILURES.labels(reason="admin_key_invalid").inc()
            raise GatewayError(
                401,
                "admin_key_invalid",
                "A valid admin API key is required.",
                headers={"WWW-Authenticate": "Bearer"},
            )

    return check


require_admin = admin_access()


async def require_identity(request: Request) -> Identity:
    started = time.perf_counter()
    key = request.app.state.settings.token_signing_key.get_secret_value()
    try:
        claims = verify_token(bearer_token(request.headers.get("authorization")), key)
        record = await teams.load_identity(request.app.state.pool, claims.employee_id)
        identity = match_identity(claims, record)
    except TokenError as exc:
        metrics.AUTH_FAILURES.labels(AUTH_FAILURE_REASONS[exc.code]).inc()
        raise GatewayError(401, exc.code, TOKEN_MESSAGES[exc.code], headers=challenge(exc.code)) from exc
    request.state.identity = identity
    request.state.auth_ms = round((time.perf_counter() - started) * 1000, 3)
    return identity
