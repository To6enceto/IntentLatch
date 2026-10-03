import json
import re
from pathlib import Path
from typing import Any, Literal, Self

import psycopg
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    StrictStr,
    ValidationError,
    field_validator,
    model_validator,
)
from pydantic_core import PydanticCustomError

from . import db
from .errors import GatewayError, describe_validation_error
from .teams import TEAM_NAME_MAX, clean_name, iso

CODE_MAX = 64
CODE_PATTERN = re.compile(r"[A-Z][A-Z0-9]*(?:-[A-Z0-9]+)*")
TEXT_MAX = 1000
PATTERN_MAX = 1000
INT_MAX = 2_147_483_647
SEEDS_FILE = Path(__file__).parent / "policy_seeds.json"

SHAPE_FIELDS = ("code", "ai", "text", "kind", "params", "action", "applies_to", "enabled")
POLICY_FIELDS = (*SHAPE_FIELDS, "created_at", "updated_at")
POLICY_COLUMNS = ", ".join(POLICY_FIELDS)
INSERT_POLICY = (
    "INSERT INTO policies (code, ai, text, kind, params, action, applies_to, enabled)"
    " VALUES (%s, %s, %s, %s, %s, %s, %s, %s)"
)

Kind = Literal["authority", "limit", "regex"]
Action = Literal["block", "edit"]
AppliesTo = Literal["prompt", "response", "both"]


def shape_error(message: str) -> PydanticCustomError:
    # The message goes in as context so braces in it are never read as a template.
    return PydanticCustomError("policy_shape", "{message}", {"message": message})


def normalize_code(value: str) -> str:
    # ASCII is checked before uppercasing, which maps some non-ASCII letters to ASCII.
    code = value.strip()
    if not (code.isascii() and 2 <= len(code) <= CODE_MAX and CODE_PATTERN.fullmatch(code.upper())):
        raise ValueError(
            f"must be 2 to {CODE_MAX} letters, digits and single hyphens, starting with a letter"
        )
    return code.upper()


class LimitParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_tokens: StrictInt = Field(ge=1, le=INT_MAX)
    window_seconds: StrictInt = Field(ge=1, le=INT_MAX)
    team: StrictStr | None

    @field_validator("team")
    @classmethod
    def _team(cls, value: str | None) -> str | None:
        return clean_name(value, TEAM_NAME_MAX) if value is not None else None


class RegexParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pattern: StrictStr = Field(min_length=1, max_length=PATTERN_MAX)

    @field_validator("pattern")
    @classmethod
    def _compiles(cls, value: str) -> str:
        if "\x00" in value:
            raise ValueError("must not contain NUL characters")
        try:
            re.compile(value)
        except re.error as exc:
            raise ValueError(f"invalid regular expression: {exc}") from exc
        return value


KIND_PARAMS: dict[str, type[BaseModel]] = {"limit": LimitParams, "regex": RegexParams}


def kind_params(model: type[BaseModel], params: dict[str, Any]) -> dict[str, Any]:
    try:
        return model.model_validate(params).model_dump()
    except ValidationError as exc:
        first = exc.errors()[0]
        location = ".".join(str(part) for part in ("params", *first["loc"]))
        raise shape_error(f"{location}: {first['msg']}") from exc


class PolicyCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: StrictStr
    ai: StrictBool
    text: StrictStr | None = None
    kind: Kind | None = None
    params: dict[str, Any] = Field(default_factory=dict)
    action: Action
    applies_to: AppliesTo | None = None
    enabled: StrictBool

    @field_validator("code")
    @classmethod
    def _code(cls, value: str) -> str:
        return normalize_code(value)

    @field_validator("text")
    @classmethod
    def _text(cls, value: str | None) -> str | None:
        return clean_name(value, TEXT_MAX) if value is not None else None

    @model_validator(mode="after")
    def _shape(self) -> Self:
        if self.ai:
            if self.text is None:
                raise shape_error("text is required when ai is true")
            if self.kind is not None:
                raise shape_error("kind is only allowed when ai is false")
        else:
            if self.text is not None:
                raise shape_error("text is only allowed when ai is true")
            if self.kind is None:
                raise shape_error("kind is required when ai is false")

        label = "AI" if self.ai else self.kind
        model = KIND_PARAMS.get(self.kind) if self.kind else None
        if model is not None:
            self.params = kind_params(model, self.params)
        elif self.params:
            raise shape_error(f"params must be empty for {label} policies")

        if self.kind in ("authority", "limit"):
            if self.action != "block":
                raise shape_error(f"action must be block for {label} policies")
            if self.applies_to not in (None, "prompt"):
                raise shape_error(f"applies_to must be prompt for {label} policies")
            self.applies_to = "prompt"
        elif self.applies_to is None:
            raise shape_error(f"applies_to is required for {label} policies")
        return self


class PolicyUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: StrictStr | None = None
    params: dict[str, Any] | None = None
    action: Action | None = None
    applies_to: AppliesTo | None = None
    enabled: StrictBool | None = None

    @model_validator(mode="after")
    def _not_empty(self) -> Self:
        if not self.model_fields_set:
            raise shape_error("at least one of text, params, action, applies_to or enabled is required")
        return self


def merge_update(current: dict[str, Any], update: PolicyUpdate) -> PolicyCreate:
    # params is replaced whole; the merged policy must pass every create rule.
    changes = update.model_dump(include=update.model_fields_set)
    return PolicyCreate.model_validate({**current, **changes})


def load_seeds(path: Path = SEEDS_FILE) -> list[PolicyCreate]:
    return [PolicyCreate.model_validate(entry) for entry in json.loads(path.read_text())]


def policy_json(row: tuple) -> dict[str, Any]:
    *shape, created_at, updated_at = row
    return {**dict(zip(SHAPE_FIELDS, shape)), "created_at": iso(created_at), "updated_at": iso(updated_at)}


def policy_not_found() -> GatewayError:
    return GatewayError(404, "policy_not_found", "Policy not found.")


async def team_params(conn: psycopg.AsyncConnection, policy: PolicyCreate) -> dict[str, Any]:
    # A limit's team is stored under the team's own name so later matching is exact.
    team = policy.params.get("team")
    if policy.kind != "limit" or team is None:
        return policy.params
    cursor = await conn.execute("SELECT name FROM teams WHERE lower(name) = lower(%s)", (team,))
    row = await cursor.fetchone()
    if row is None:
        raise GatewayError(400, "invalid_request", "params.team: no team with this name")
    return {**policy.params, "team": row[0]}


def insert_values(policy: PolicyCreate, params: dict[str, Any]) -> tuple:
    return (
        policy.code,
        policy.ai,
        policy.text,
        policy.kind,
        Jsonb(params),
        policy.action,
        policy.applies_to,
        policy.enabled,
    )


async def bump_version(conn: psycopg.AsyncConnection) -> int:
    # Taken last in every write, so concurrent writers serialize here in commit order.
    cursor = await conn.execute("UPDATE policy_version SET version = version + 1 RETURNING version")
    row = await cursor.fetchone()
    return row[0]


async def create_policy(pool: AsyncConnectionPool, data: PolicyCreate) -> dict[str, Any]:
    try:
        async with db.connection(pool) as conn:
            params = await team_params(conn, data)
            cursor = await conn.execute(
                f"{INSERT_POLICY} RETURNING {POLICY_COLUMNS}", insert_values(data, params)
            )
            row = await cursor.fetchone()
            version = await bump_version(conn)
    except psycopg.errors.UniqueViolation as exc:
        raise GatewayError(409, "policy_exists", "A policy with this code already exists.") from exc
    return {"policy": policy_json(row), "version": version}


async def list_policies(pool: AsyncConnectionPool) -> dict[str, Any]:
    columns = ", ".join(f"p.{field}" for field in POLICY_FIELDS)
    async with db.connection(pool) as conn:
        # One statement, so the version matches the rows it is listed with.
        cursor = await conn.execute(
            f"SELECT v.version, {columns} FROM policy_version v"
            ' LEFT JOIN policies p ON true ORDER BY p.code COLLATE "C"'
        )
        rows = await cursor.fetchall()
    policies = [policy_json(row[1:]) for row in rows if row[1] is not None]
    return {"policies": policies, "version": rows[0][0]}


async def update_policy(pool: AsyncConnectionPool, code: str, update: PolicyUpdate) -> dict[str, Any]:
    # A path that cannot be a code is simply not found, and never reaches the query.
    try:
        code = normalize_code(code)
    except ValueError:
        raise policy_not_found() from None
    async with db.connection(pool) as conn:
        cursor = await conn.execute(
            f"SELECT {', '.join(SHAPE_FIELDS)} FROM policies WHERE code = %s FOR UPDATE",
            (code,),
        )
        current = await cursor.fetchone()
        if current is None:
            raise policy_not_found()
        try:
            policy = merge_update(dict(zip(SHAPE_FIELDS, current)), update)
        except ValidationError as exc:
            raise GatewayError(400, "invalid_request", describe_validation_error(exc)) from exc
        params = await team_params(conn, policy)
        cursor = await conn.execute(
            "UPDATE policies SET text = %s, params = %s, action = %s, applies_to = %s,"
            f" enabled = %s, updated_at = now() WHERE code = %s RETURNING {POLICY_COLUMNS}",
            (policy.text, Jsonb(params), policy.action, policy.applies_to, policy.enabled, policy.code),
        )
        row = await cursor.fetchone()
        version = await bump_version(conn)
    return {"policy": policy_json(row), "version": version}


async def seed_policies(pool: AsyncConnectionPool, seeds: list[PolicyCreate]) -> int:
    # Inserts missing codes only, so admin edits to a seeded policy survive restarts.
    inserted = 0
    async with db.connection(pool) as conn:
        for policy in seeds:
            params = await team_params(conn, policy)
            cursor = await conn.execute(
                f"{INSERT_POLICY} ON CONFLICT (code) DO NOTHING RETURNING code",
                insert_values(policy, params),
            )
            if await cursor.fetchone() is not None:
                inserted += 1
        if inserted:
            await bump_version(conn)
    return inserted
