import time
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

import httpx
from fastapi import FastAPI
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool
from pydantic import BaseModel, ConfigDict, Field, StrictStr, field_validator

from . import db, metrics, teams
from .errors import GatewayError
from .identity import EmployeeRecord, issue_token
from .pipeline import elapsed_ms
from .policies import normalize_code
from .teams import clean_name, iso
from .testcases import Case, storable

TOKEN_TTL_SECONDS = 60
STARTED_BY_MAX = 64
FAILURE_MAX = 300
RUNS_LISTED = 50
# The runner calls its own app in-process, so this host never reaches the network.
INTERNAL_URL = "http://intentlatch.internal"
OUTCOMES = {"allowed": "ALLOW", "edited": "EDIT", "blocked": "BLOCK"}
RUN_COLUMNS = "id, started_at, finished_at, started_by, mode, policy_version, status, totals"
RESULT_FIELDS = (
    "case_code",
    "expected",
    "actual",
    "fired_policy_codes",
    "passed",
    "failure",
    "policy_version",
    "duration_ms",
)
INSERT_RESULT = (
    f"INSERT INTO test_case_results (run_id, {', '.join(RESULT_FIELDS)})"
    f" VALUES ({', '.join(['%s'] * (len(RESULT_FIELDS) + 1))})"
)

Actual = Literal["ALLOW", "EDIT", "BLOCK", "ERROR"]


class RunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cases: list[StrictStr] | None = Field(default=None, min_length=1)
    mode: Literal["check"] = "check"
    started_by: StrictStr = "api"

    @field_validator("cases")
    @classmethod
    def _cases(cls, values: list[str] | None) -> list[str] | None:
        return list(dict.fromkeys(normalize_code(value) for value in values)) if values is not None else None

    @field_validator("started_by")
    @classmethod
    def _started_by(cls, value: str) -> str:
        return storable(clean_name(value, STARTED_BY_MAX))


class CaseResult(BaseModel):
    case_code: str
    expected: str
    actual: Actual
    fired_policy_codes: list[str]
    passed: bool
    failure: str | None
    policy_version: int | None
    duration_ms: float


def mint_token(record: EmployeeRecord, key: str, now: datetime) -> str:
    """A short-lived token for the case's employee; it never leaves this process."""
    # The employee's current token id keeps revocation and reissue in force for test runs.
    issued_at = now.replace(microsecond=0)
    return issue_token(
        employee_id=record.employee_id,
        team=record.team_name,
        models=record.authorized_models,
        token_id=record.token_id,
        issued_at=issued_at,
        key=key,
        expires_at=issued_at + timedelta(seconds=TOKEN_TTL_SECONDS),
    )


def error_result(case: Case, failure: str, duration_ms: float = 0.0) -> CaseResult:
    return CaseResult(
        case_code=case.code,
        expected=case.expected,
        actual="ERROR",
        fired_policy_codes=[],
        passed=False,
        failure=failure[:FAILURE_MAX],
        policy_version=None,
        duration_ms=round(duration_ms, 3),
    )


def answer_failure(status_code: int, body: Any) -> str:
    error = body.get("error") if isinstance(body, dict) else None
    if isinstance(error, dict):
        return f"The check answered {status_code} {error.get('code')}: {error.get('message')}"
    return f"The check answered {status_code} without a decision."


def decision_failure(case: Case, actual: str, fired: list[str], decision: dict[str, Any]) -> str | None:
    if decision.get("control_agent_status") == "error":
        return "The control agent failed, so the policies were not fully checked."
    if actual != case.expected:
        return f"Expected {case.expected}, got {actual}."
    if case.expected_policy_code is not None and case.expected_policy_code not in fired:
        return f"Expected {case.expected_policy_code} to fire; fired: {', '.join(fired) or 'none'}."
    rewritten = decision.get("rewritten")
    folded = rewritten.casefold() if isinstance(rewritten, str) else ""
    for position, text in enumerate(case.must_not_contain, start=1):
        if text.casefold() in folded:
            return f"The rewritten prompt still contains must_not_contain entry {position}."
    return None


def judge(case: Case, status_code: int, body: Any, duration_ms: float) -> CaseResult:
    """Compares one /check answer with the case; the first failing rule is the failure."""
    if status_code != 200 or not isinstance(body, dict) or body.get("outcome") not in OUTCOMES:
        return error_result(case, answer_failure(status_code, body), duration_ms)
    actual = OUTCOMES[body["outcome"]]
    responsible = body.get("responsible")
    fired = [code for code in responsible if isinstance(code, str)] if isinstance(responsible, list) else []
    failure = decision_failure(case, actual, fired, body)
    version = body.get("policy_version")
    return CaseResult(
        case_code=case.code,
        expected=case.expected,
        actual=actual,
        fired_policy_codes=fired,
        passed=failure is None,
        failure=failure,
        policy_version=version if isinstance(version, int) and not isinstance(version, bool) else None,
        duration_ms=round(duration_ms, 3),
    )


async def check_case(client: httpx.AsyncClient, case: Case, token: str) -> CaseResult:
    started = time.perf_counter()
    try:
        response = await client.post(
            "/check",
            json={"model": case.model, "prompt": case.prompt},
            headers={"Authorization": f"Bearer {token}"},
        )
    except httpx.HTTPError:
        return error_result(case, "The check could not run.", elapsed_ms(started))
    try:
        body = response.json()
    except ValueError:
        body = None
    return judge(case, response.status_code, body, elapsed_ms(started))


async def run_case(client: httpx.AsyncClient, pool: AsyncConnectionPool, key: str, case: Case) -> CaseResult:
    record = await teams.load_identity(pool, case.run_as_employee_id)
    if record is None:
        return error_result(case, "The case's employee does not exist.")
    return await check_case(client, case, mint_token(record, key, datetime.now(UTC)))


def totals(results: list[CaseResult]) -> dict[str, int]:
    passed = sum(result.passed for result in results)
    return {"total": len(results), "passed": passed, "failed": len(results) - passed}


def run_status(results: list[CaseResult]) -> str:
    # An empty run fails, so "nothing ran" never reads as "passed".
    return "passed" if results and all(result.passed for result in results) else "failed"


def count(result: CaseResult) -> None:
    metrics.TEST_CASES.labels("passed" if result.passed else "failed").inc()


def run_json(row: tuple) -> dict[str, Any]:
    run_id, started_at, finished_at, started_by, mode, version, status, run_totals = row
    return {
        "id": str(run_id),
        "started_at": iso(started_at),
        "finished_at": iso(finished_at),
        "started_by": started_by,
        "mode": mode,
        "policy_version": version,
        "status": status,
        "totals": run_totals,
    }


def result_json(row: tuple) -> dict[str, Any]:
    result = dict(zip(RESULT_FIELDS, row))
    return result | {"fired_policy_codes": list(result["fired_policy_codes"])}


async def start_run(pool: AsyncConnectionPool, started_by: str, case_count: int) -> uuid.UUID:
    run_id = uuid.uuid4()
    async with db.connection(pool) as conn:
        # The version in force when the run starts; each result also carries the one it was checked against.
        await conn.execute(
            "INSERT INTO test_runs (id, started_by, mode, policy_version, status, totals)"
            " SELECT %s, %s, 'check', version, 'running', %s FROM policy_version",
            (run_id, started_by, Jsonb({"total": case_count, "passed": 0, "failed": 0})),
        )
    return run_id


async def save_result(pool: AsyncConnectionPool, run_id: uuid.UUID, result: CaseResult) -> None:
    async with db.connection(pool) as conn:
        await conn.execute(INSERT_RESULT, (run_id, *(getattr(result, field) for field in RESULT_FIELDS)))


async def finish_run(pool: AsyncConnectionPool, run_id: uuid.UUID, results: list[CaseResult]) -> dict[str, Any]:
    async with db.connection(pool) as conn:
        cursor = await conn.execute(
            "UPDATE test_runs SET finished_at = now(), status = %s, totals = %s"
            f" WHERE id = %s RETURNING {RUN_COLUMNS}",
            (run_status(results), Jsonb(totals(results)), run_id),
        )
        row = await cursor.fetchone()
    return run_json(row) | {"results": [result.model_dump() for result in results]}


async def execute(app: FastAPI, cases: list[Case], started_by: str) -> dict[str, Any]:
    """Runs the cases through /check one at a time, storing and counting each result as it ends."""
    pool = app.state.pool
    key = app.state.settings.token_signing_key.get_secret_value()
    run_id = await start_run(pool, started_by, len(cases))
    results = []
    # Through the app itself, so token checks, middleware and the pipeline run as for any client.
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url=INTERNAL_URL, timeout=None) as client:
        for case in cases:
            result = await run_case(client, pool, key, case)
            await save_result(pool, run_id, result)
            count(result)
            results.append(result)
    return await finish_run(pool, run_id, results)


async def get_run(pool: AsyncConnectionPool, run_id: uuid.UUID) -> dict[str, Any]:
    async with db.connection(pool) as conn:
        cursor = await conn.execute(f"SELECT {RUN_COLUMNS} FROM test_runs WHERE id = %s", (run_id,))
        row = await cursor.fetchone()
        if row is None:
            raise GatewayError(404, "test_run_not_found", "Test run not found.")
        cursor = await conn.execute(
            f"SELECT {', '.join(RESULT_FIELDS)} FROM test_case_results"
            ' WHERE run_id = %s ORDER BY case_code COLLATE "C"',
            (run_id,),
        )
        results = await cursor.fetchall()
    return run_json(row) | {"results": [result_json(result) for result in results]}


async def list_runs(pool: AsyncConnectionPool) -> list[dict[str, Any]]:
    async with db.connection(pool) as conn:
        cursor = await conn.execute(
            f"SELECT {RUN_COLUMNS} FROM test_runs ORDER BY started_at DESC, id LIMIT %s", (RUNS_LISTED,)
        )
        rows = await cursor.fetchall()
    return [run_json(row) for row in rows]
