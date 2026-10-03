from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from .. import teams
from ..identity import Identity, TokenError, match_identity, verify_token

router = APIRouter()

NO_STORE = {"Cache-Control": "no-store"}


class AuthorityRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    token: str = Field(min_length=1)


def valid_body(identity: Identity) -> dict[str, Any]:
    return {
        "valid": True,
        "employee": {"id": str(identity.employee_id), "name": identity.employee_name},
        "team": {"id": str(identity.team_id), "name": identity.team_name},
        "authorized_models": list(identity.authorized_models),
    }


def invalid_body(reason: str) -> dict[str, Any]:
    return {"valid": False, "reason": reason}


@router.post("/authority")
async def authority(body: AuthorityRequest, request: Request) -> JSONResponse:
    key = request.app.state.settings.token_signing_key.get_secret_value()
    try:
        claims = verify_token(body.token, key)
        record = await teams.load_identity(request.app.state.pool, claims.employee_id)
        identity = match_identity(claims, record)
    except TokenError as exc:
        return JSONResponse(invalid_body(exc.code), headers=NO_STORE)
    return JSONResponse(valid_body(identity), headers=NO_STORE)
