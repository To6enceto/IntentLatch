import hmac
import time

from fastapi import Request

from . import teams
from .errors import GatewayError
from .identity import Identity, TokenError, bearer_token, match_identity, verify_token

TOKEN_MESSAGES = {
    "token_missing": "An employee token is required: send Authorization: Bearer <token>.",
    "token_invalid": "The employee token is not valid.",
    "token_revoked": "The employee token has been revoked or replaced.",
}


def challenge(code: str) -> dict[str, str]:
    if code == "token_missing":
        return {"WWW-Authenticate": "Bearer"}
    return {"WWW-Authenticate": 'Bearer error="invalid_token"'}


async def require_admin(request: Request) -> None:
    expected = request.app.state.settings.admin_api_key.get_secret_value().encode()
    try:
        supplied = bearer_token(request.headers.get("authorization")).encode()
    except TokenError:
        supplied = b""
    if not hmac.compare_digest(supplied, expected):
        raise GatewayError(
            401,
            "admin_key_invalid",
            "A valid admin API key is required.",
            headers={"WWW-Authenticate": "Bearer"},
        )


async def require_identity(request: Request) -> Identity:
    started = time.perf_counter()
    key = request.app.state.settings.token_signing_key.get_secret_value()
    try:
        claims = verify_token(bearer_token(request.headers.get("authorization")), key)
        record = await teams.load_identity(request.app.state.pool, claims.employee_id)
        identity = match_identity(claims, record)
    except TokenError as exc:
        raise GatewayError(401, exc.code, TOKEN_MESSAGES[exc.code], headers=challenge(exc.code)) from exc
    request.state.identity = identity
    request.state.auth_ms = round((time.perf_counter() - started) * 1000, 3)
    return identity
