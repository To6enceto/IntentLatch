import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

import jwt

ALGORITHM = "HS256"
REQUIRED_CLAIMS = ["sub", "jti", "iat"]


class TokenError(Exception):
    """Carries one of: token_missing, token_invalid, token_revoked."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class TokenClaims:
    employee_id: uuid.UUID
    token_id: uuid.UUID


@dataclass(frozen=True)
class EmployeeRecord:
    employee_id: uuid.UUID
    employee_name: str
    token_id: uuid.UUID
    revoked_at: datetime | None
    team_id: uuid.UUID
    team_name: str
    authorized_models: list[str]


@dataclass(frozen=True)
class Identity:
    employee_id: uuid.UUID
    employee_name: str
    team_id: uuid.UUID
    team_name: str
    authorized_models: list[str]


def issue_token(
    *,
    employee_id: uuid.UUID,
    team: str,
    models: Sequence[str],
    token_id: uuid.UUID,
    issued_at: datetime,
    key: str,
    expires_at: datetime | None = None,
) -> str:
    payload = {
        "sub": str(employee_id),
        "team": team,
        "models": list(models),
        "jti": str(token_id),
        "iat": int(issued_at.timestamp()),
    }
    # Employee tokens never expire; only the test runner's minted tokens carry exp, which decode enforces.
    if expires_at is not None:
        payload["exp"] = int(expires_at.timestamp())
    return jwt.encode(payload, key, algorithm=ALGORITHM)


def verify_token(token: str, key: str) -> TokenClaims:
    try:
        payload = jwt.decode(
            token, key, algorithms=[ALGORITHM], options={"require": REQUIRED_CLAIMS}
        )
        return TokenClaims(
            employee_id=uuid.UUID(str(payload["sub"])), token_id=uuid.UUID(str(payload["jti"]))
        )
    except (jwt.PyJWTError, ValueError) as exc:
        raise TokenError("token_invalid") from exc


def bearer_token(header: str | None) -> str:
    scheme, _, value = (header or "").strip().partition(" ")
    token = value.strip()
    if scheme.lower() != "bearer" or not token:
        raise TokenError("token_missing")
    return token


def match_identity(claims: TokenClaims, record: EmployeeRecord | None) -> Identity:
    if record is None or record.employee_id != claims.employee_id:
        raise TokenError("token_invalid")
    if record.revoked_at is not None or record.token_id != claims.token_id:
        raise TokenError("token_revoked")
    return Identity(
        employee_id=record.employee_id,
        employee_name=record.employee_name,
        team_id=record.team_id,
        team_name=record.team_name,
        authorized_models=list(record.authorized_models),
    )
