import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

import psycopg
from psycopg_pool import AsyncConnectionPool
from pydantic import BaseModel, ConfigDict, field_validator

from . import db
from .errors import GatewayError
from .identity import EmployeeRecord, issue_token
from .llms import MODEL_IDS

TEAM_NAME_MAX = 64
EMPLOYEE_NAME_MAX = 128

EMPLOYEE_COLUMNS = (
    "e.id, e.team_id, t.name, e.name, e.token_issued_at, e.revoked_at, e.created_at, "
    "t.authorized_models"
)


def clean_name(value: str, limit: int) -> str:
    name = value.strip()
    if not 1 <= len(name) <= limit:
        raise ValueError(f"must be 1 to {limit} characters after trimming")
    # PostgreSQL text cannot hold NUL, so it is a validation error, not a 500.
    if "\x00" in name:
        raise ValueError("must not contain NUL characters")
    return name


def normalize_models(values: Sequence[str]) -> list[str]:
    if not values:
        raise ValueError("must contain at least one model")
    if any(value not in MODEL_IDS for value in values):
        raise ValueError(f"may only contain {', '.join(MODEL_IDS)}")
    return [model for model in MODEL_IDS if model in values]


class TeamCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    authorized_models: list[str]

    @field_validator("name")
    @classmethod
    def _name(cls, value: str) -> str:
        return clean_name(value, TEAM_NAME_MAX)

    @field_validator("authorized_models")
    @classmethod
    def _models(cls, value: list[str]) -> list[str]:
        return normalize_models(value)


class TeamUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    authorized_models: list[str]

    @field_validator("authorized_models")
    @classmethod
    def _models(cls, value: list[str]) -> list[str]:
        return normalize_models(value)


class EmployeeCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str

    @field_validator("name")
    @classmethod
    def _name(cls, value: str) -> str:
        return clean_name(value, EMPLOYEE_NAME_MAX)


def iso(value: datetime | None) -> str | None:
    return value.astimezone(UTC).isoformat() if value is not None else None


def team_json(row: tuple) -> dict[str, Any]:
    team_id, name, models, created_at = row
    return {"id": str(team_id), "name": name, "authorized_models": list(models), "created_at": iso(created_at)}


def employee_json(row: tuple) -> dict[str, Any]:
    employee_id, team_id, team_name, name, issued_at, revoked_at, created_at = row[:7]
    return {
        "id": str(employee_id),
        "team_id": str(team_id),
        "team": team_name,
        "name": name,
        "token_issued_at": iso(issued_at),
        "revoked_at": iso(revoked_at),
        "created_at": iso(created_at),
    }


def employee_token(row: tuple, token_id: uuid.UUID, key: str) -> str:
    employee_id, _, team_name, _, issued_at = row[:5]
    return issue_token(
        employee_id=employee_id,
        team=team_name,
        models=row[7],
        token_id=token_id,
        issued_at=issued_at,
        key=key,
    )


def team_not_found() -> GatewayError:
    return GatewayError(404, "team_not_found", "Team not found.")


def employee_not_found() -> GatewayError:
    return GatewayError(404, "employee_not_found", "Employee not found.")


async def create_team(pool: AsyncConnectionPool, data: TeamCreate) -> dict[str, Any]:
    try:
        async with db.connection(pool) as conn:
            cursor = await conn.execute(
                "INSERT INTO teams (name, authorized_models) VALUES (%s, %s)"
                " RETURNING id, name, authorized_models, created_at",
                (data.name, data.authorized_models),
            )
            row = await cursor.fetchone()
    except psycopg.errors.UniqueViolation as exc:
        raise GatewayError(409, "team_exists", "A team with this name already exists.") from exc
    return team_json(row)


async def list_teams(pool: AsyncConnectionPool) -> list[dict[str, Any]]:
    async with db.connection(pool) as conn:
        cursor = await conn.execute(
            "SELECT id, name, authorized_models, created_at FROM teams ORDER BY created_at, id"
        )
        rows = await cursor.fetchall()
    return [team_json(row) for row in rows]


async def update_team(pool: AsyncConnectionPool, team_id: uuid.UUID, data: TeamUpdate) -> dict[str, Any]:
    async with db.connection(pool) as conn:
        cursor = await conn.execute(
            "UPDATE teams SET authorized_models = %s WHERE id = %s"
            " RETURNING id, name, authorized_models, created_at",
            (data.authorized_models, team_id),
        )
        row = await cursor.fetchone()
    if row is None:
        raise team_not_found()
    return team_json(row)


async def create_employee(
    pool: AsyncConnectionPool, team_id: uuid.UUID, data: EmployeeCreate, key: str
) -> tuple[dict[str, Any], str]:
    token_id = uuid.uuid4()
    issued_at = datetime.now(UTC).replace(microsecond=0)
    async with db.connection(pool) as conn:
        cursor = await conn.execute(
            "WITH inserted AS ("
            " INSERT INTO employees (team_id, name, token_id, token_issued_at)"
            " SELECT id, %s, %s, %s FROM teams WHERE id = %s"
            " RETURNING *)"
            f" SELECT {EMPLOYEE_COLUMNS} FROM inserted e JOIN teams t ON t.id = e.team_id",
            (data.name, token_id, issued_at, team_id),
        )
        row = await cursor.fetchone()
    if row is None:
        raise team_not_found()
    return employee_json(row), employee_token(row, token_id, key)


async def list_employees(pool: AsyncConnectionPool, team_id: uuid.UUID) -> list[dict[str, Any]]:
    async with db.connection(pool) as conn:
        cursor = await conn.execute("SELECT 1 FROM teams WHERE id = %s", (team_id,))
        if await cursor.fetchone() is None:
            raise team_not_found()
        cursor = await conn.execute(
            f"SELECT {EMPLOYEE_COLUMNS} FROM employees e JOIN teams t ON t.id = e.team_id"
            " WHERE e.team_id = %s ORDER BY e.created_at, e.id",
            (team_id,),
        )
        rows = await cursor.fetchall()
    return [employee_json(row) for row in rows]


async def reissue_token(
    pool: AsyncConnectionPool, employee_id: uuid.UUID, key: str
) -> tuple[dict[str, Any], str]:
    token_id = uuid.uuid4()
    issued_at = datetime.now(UTC).replace(microsecond=0)
    async with db.connection(pool) as conn:
        cursor = await conn.execute(
            "UPDATE employees e SET token_id = %s, token_issued_at = %s FROM teams t"
            " WHERE e.id = %s AND e.revoked_at IS NULL AND t.id = e.team_id"
            f" RETURNING {EMPLOYEE_COLUMNS}",
            (token_id, issued_at, employee_id),
        )
        row = await cursor.fetchone()
        if row is None:
            cursor = await conn.execute("SELECT 1 FROM employees WHERE id = %s", (employee_id,))
            if await cursor.fetchone() is None:
                raise employee_not_found()
            raise GatewayError(409, "employee_revoked", "This employee is revoked; create a new employee instead.")
    return employee_json(row), employee_token(row, token_id, key)


async def revoke(pool: AsyncConnectionPool, employee_id: uuid.UUID) -> dict[str, Any]:
    async with db.connection(pool) as conn:
        cursor = await conn.execute(
            "UPDATE employees e SET revoked_at = coalesce(e.revoked_at, now()) FROM teams t"
            f" WHERE e.id = %s AND t.id = e.team_id RETURNING {EMPLOYEE_COLUMNS}",
            (employee_id,),
        )
        row = await cursor.fetchone()
    if row is None:
        raise employee_not_found()
    return employee_json(row)


async def load_identity(pool: AsyncConnectionPool, employee_id: uuid.UUID) -> EmployeeRecord | None:
    async with db.connection(pool) as conn:
        cursor = await conn.execute(
            "SELECT e.id, e.name, e.token_id, e.revoked_at, t.id, t.name, t.authorized_models"
            " FROM employees e JOIN teams t ON t.id = e.team_id WHERE e.id = %s",
            (employee_id,),
        )
        row = await cursor.fetchone()
    return EmployeeRecord(*row) if row is not None else None
