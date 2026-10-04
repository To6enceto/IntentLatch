import json
import uuid
from pathlib import Path
from typing import Any, Literal, Self

import psycopg
from psycopg_pool import AsyncConnectionPool
from pydantic import BaseModel, ConfigDict, Field, StrictStr, ValidationError, field_validator, model_validator

from . import db
from .errors import GatewayError, describe_validation_error
from .llms import MODEL_A, MODEL_IDS
from .policies import normalize_code, shape_error
from .teams import clean_name, iso

NAME_MAX = 128
PROMPT_MAX = 4000
MUST_NOT_CONTAIN_MAX = 20
ENTRY_MAX = 200
SEEDS_FILE = Path(__file__).parent / "testcase_seeds.json"

CASE_FIELDS = (
    "code",
    "name",
    "prompt",
    "model",
    "run_as_employee_id",
    "expected",
    "expected_policy_code",
    "must_not_contain",
)
CASE_COLUMNS = ", ".join((*CASE_FIELDS, "predefined", "created_at", "updated_at"))
RUN_FIELDS = tuple(field for field in CASE_FIELDS if field != "name")
INSERT_CASE = (
    f"INSERT INTO test_cases ({', '.join(CASE_FIELDS)}, predefined)"
    f" VALUES ({', '.join(['%s'] * (len(CASE_FIELDS) + 1))})"
)
UPDATE_CASE = (
    f"UPDATE test_cases SET {', '.join(f'{field} = %s' for field in CASE_FIELDS[1:])}, updated_at = now()"
    f" WHERE code = %s RETURNING {CASE_COLUMNS}"
)

TEST_TEAM_NAME = "intentlatch-tests"
# Corporate A only, so the predefined authority case can ask for corporate B.
TEST_TEAM_MODELS = [MODEL_A]
TEST_EMPLOYEE_NAME = "test-runner"
# Fixed, so seeding stays idempotent across restarts and replicas.
TEST_EMPLOYEE_ID = uuid.UUID("7e57ca5e-0000-4000-8000-000000000001")

Expected = Literal["ALLOW", "EDIT", "BLOCK"]


def storable(value: str) -> str:
    # PostgreSQL text holds neither NUL nor an unpaired surrogate, so both are validation errors.
    if "\x00" in value:
        raise ValueError("must not contain NUL characters")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        raise ValueError("must not contain unpaired surrogate characters") from None
    return value


class CaseCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: StrictStr
    name: StrictStr
    prompt: StrictStr = Field(min_length=1, max_length=PROMPT_MAX)
    model: StrictStr
    run_as_employee_id: uuid.UUID = TEST_EMPLOYEE_ID
    expected: Expected
    expected_policy_code: StrictStr | None = None
    must_not_contain: list[StrictStr] = Field(default_factory=list, max_length=MUST_NOT_CONTAIN_MAX)

    @field_validator("code")
    @classmethod
    def _code(cls, value: str) -> str:
        return normalize_code(value)

    @field_validator("name")
    @classmethod
    def _name(cls, value: str) -> str:
        return storable(clean_name(value, NAME_MAX))

    @field_validator("prompt")
    @classmethod
    def _prompt(cls, value: str) -> str:
        # Kept verbatim: edge characters can be exactly what a regex case tests.
        if not value.strip():
            raise ValueError("must not be blank")
        return storable(value)

    @field_validator("model")
    @classmethod
    def _model(cls, value: str) -> str:
        if value not in MODEL_IDS:
            raise ValueError(f"must be one of {', '.join(MODEL_IDS)}")
        return value

    @field_validator("expected_policy_code")
    @classmethod
    def _policy_code(cls, value: str | None) -> str | None:
        return normalize_code(value) if value is not None else None

    @field_validator("must_not_contain")
    @classmethod
    def _must_not_contain(cls, values: list[str]) -> list[str]:
        return [storable(clean_name(value, ENTRY_MAX)) for value in values]

    @model_validator(mode="after")
    def _shape(self) -> Self:
        if self.expected == "ALLOW" and self.expected_policy_code is not None:
            raise shape_error("expected_policy_code is only allowed when expected is EDIT or BLOCK")
        if self.must_not_contain and self.expected != "EDIT":
            raise shape_error("must_not_contain is only allowed when expected is EDIT")
        return self


class CaseUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: StrictStr | None = None
    prompt: StrictStr | None = None
    model: StrictStr | None = None
    run_as_employee_id: uuid.UUID | None = None
    expected: Expected | None = None
    expected_policy_code: StrictStr | None = None
    must_not_contain: list[StrictStr] | None = None

    @model_validator(mode="after")
    def _not_empty(self) -> Self:
        if not self.model_fields_set:
            raise shape_error(
                "at least one of name, prompt, model, run_as_employee_id, expected,"
                " expected_policy_code or must_not_contain is required"
            )
        return self


def merge_case_update(current: dict[str, Any], update: CaseUpdate) -> CaseCreate:
    # The merged case must pass every create rule; code and predefined never change.
    changes = update.model_dump(include=update.model_fields_set)
    return CaseCreate.model_validate({**current, **changes})


class Case(BaseModel):
    """A stored case as the runner sees it."""

    code: str
    prompt: str
    model: str
    run_as_employee_id: uuid.UUID
    expected: Expected
    expected_policy_code: str | None
    must_not_contain: list[str]


def load_seeds(path: Path = SEEDS_FILE) -> list[CaseCreate]:
    return [CaseCreate.model_validate(entry) for entry in json.loads(path.read_text())]


def field_values(case: CaseCreate) -> tuple:
    return tuple(getattr(case, field) for field in CASE_FIELDS)


def case_json(row: tuple) -> dict[str, Any]:
    *fields, predefined, created_at, updated_at = row
    case = dict(zip(CASE_FIELDS, fields))
    return {
        **case,
        "run_as_employee_id": str(case["run_as_employee_id"]),
        "must_not_contain": list(case["must_not_contain"]),
        "predefined": predefined,
        "created_at": iso(created_at),
        "updated_at": iso(updated_at),
    }


def case_not_found() -> GatewayError:
    return GatewayError(404, "test_case_not_found", "Test case not found.")


def unknown_employee() -> GatewayError:
    return GatewayError(400, "invalid_request", "run_as_employee_id: no employee with this id")


def missing_codes(requested: list[str] | None, cases: list[Case]) -> list[str]:
    return sorted(set(requested or []) - {case.code for case in cases})


async def create_case(pool: AsyncConnectionPool, data: CaseCreate) -> dict[str, Any]:
    try:
        async with db.connection(pool) as conn:
            cursor = await conn.execute(f"{INSERT_CASE} RETURNING {CASE_COLUMNS}", (*field_values(data), False))
            row = await cursor.fetchone()
    except psycopg.errors.UniqueViolation as exc:
        raise GatewayError(409, "test_case_exists", "A test case with this code already exists.") from exc
    except psycopg.errors.ForeignKeyViolation as exc:
        raise unknown_employee() from exc
    return case_json(row)


async def list_cases(pool: AsyncConnectionPool) -> list[dict[str, Any]]:
    async with db.connection(pool) as conn:
        cursor = await conn.execute(f'SELECT {CASE_COLUMNS} FROM test_cases ORDER BY code COLLATE "C"')
        rows = await cursor.fetchall()
    return [case_json(row) for row in rows]


async def update_case(pool: AsyncConnectionPool, code: str, update: CaseUpdate) -> dict[str, Any]:
    # A path that cannot be a code is simply not found, and never reaches the query.
    try:
        code = normalize_code(code)
    except ValueError:
        raise case_not_found() from None
    try:
        async with db.connection(pool) as conn:
            cursor = await conn.execute(
                f"SELECT {', '.join(CASE_FIELDS)} FROM test_cases WHERE code = %s FOR UPDATE", (code,)
            )
            current = await cursor.fetchone()
            if current is None:
                raise case_not_found()
            try:
                case = merge_case_update(dict(zip(CASE_FIELDS, current)), update)
            except ValidationError as exc:
                raise GatewayError(400, "invalid_request", describe_validation_error(exc)) from exc
            cursor = await conn.execute(UPDATE_CASE, (*field_values(case)[1:], case.code))
            row = await cursor.fetchone()
    except psycopg.errors.ForeignKeyViolation as exc:
        raise unknown_employee() from exc
    return case_json(row)


async def load_cases(pool: AsyncConnectionPool, codes: list[str] | None) -> list[Case]:
    """The cases to run in code order: the named ones, or every case when codes is None."""
    query = f"SELECT {', '.join(RUN_FIELDS)} FROM test_cases"
    params: tuple = ()
    if codes is not None:
        query, params = f"{query} WHERE code = ANY(%s)", (codes,)
    async with db.connection(pool) as conn:
        cursor = await conn.execute(f'{query} ORDER BY code COLLATE "C"', params)
        rows = await cursor.fetchall()
    cases = [Case(**dict(zip(RUN_FIELDS, row))) for row in rows]
    missing = missing_codes(codes, cases)
    if missing:
        raise GatewayError(400, "invalid_request", f"cases: unknown test case code(s): {', '.join(missing)}")
    return cases


async def seed_cases(pool: AsyncConnectionPool, seeds: list[CaseCreate]) -> int:
    """Creates the test team and employee when missing, then inserts missing predefined cases."""
    inserted = 0
    async with db.connection(pool) as conn:
        await conn.execute(
            "INSERT INTO teams (name, authorized_models) VALUES (%s, %s) ON CONFLICT DO NOTHING",
            (TEST_TEAM_NAME, TEST_TEAM_MODELS),
        )
        # A team that already has this name, whoever created it, is the test team.
        cursor = await conn.execute("SELECT id FROM teams WHERE lower(name) = lower(%s)", (TEST_TEAM_NAME,))
        team_id = (await cursor.fetchone())[0]
        # Its token is never issued: the runner mints a short-lived one per case.
        await conn.execute(
            "INSERT INTO employees (id, team_id, name, token_id, token_issued_at)"
            " VALUES (%s, %s, %s, %s, now()) ON CONFLICT (id) DO NOTHING",
            (TEST_EMPLOYEE_ID, team_id, TEST_EMPLOYEE_NAME, uuid.uuid4()),
        )
        # Missing codes only, so admin edits to a predefined case survive restarts.
        for case in seeds:
            cursor = await conn.execute(
                f"{INSERT_CASE} ON CONFLICT (code) DO NOTHING RETURNING code", (*field_values(case), True)
            )
            if await cursor.fetchone() is not None:
                inserted += 1
    return inserted
