from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse

from .. import console_users, metrics
from ..auth import require_console_header, require_console_user
from ..console_users import SESSION_COOKIE, ConsoleUser, LoginRequest, user_json
from ..errors import GatewayError

router = APIRouter(prefix="/console", dependencies=[Depends(require_console_header)])

NO_STORE = {"Cache-Control": "no-store"}


def is_https(request: Request) -> bool:
    # A spoofed X-Forwarded-Proto can only add the Secure flag, never remove it.
    return request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https"


def set_session_cookie(response: Response, request: Request, token: str) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=console_users.SESSION_SECONDS,
        path="/",
        secure=is_https(request),
        httponly=True,
        samesite="strict",
    )


@router.post("/session")
async def sign_in(body: LoginRequest, request: Request) -> JSONResponse:
    pool = request.app.state.pool
    user = await console_users.authenticate(pool, body.username, body.password)
    if user is None:
        metrics.AUTH_FAILURES.labels(reason="login_failed").inc()
        raise GatewayError(401, "login_failed", "The username or password is incorrect.")
    response = JSONResponse({"user": user_json(user)}, headers=NO_STORE)
    set_session_cookie(response, request, await console_users.create_session(pool, user.id))
    return response


@router.get("/session")
async def current_session(user: ConsoleUser = Depends(require_console_user)) -> JSONResponse:
    return JSONResponse({"user": user_json(user)}, headers=NO_STORE)


@router.delete("/session", status_code=204)
async def sign_out(request: Request) -> Response:
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        await console_users.delete_session(request.app.state.pool, token)
    response = Response(status_code=204, headers=NO_STORE)
    response.delete_cookie(SESSION_COOKIE, path="/", secure=is_https(request), httponly=True, samesite="strict")
    return response
