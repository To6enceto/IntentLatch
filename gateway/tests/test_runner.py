import json
import uuid
from datetime import UTC, datetime, timedelta

import httpx
import jwt
import pytest
from pydantic import ValidationError

from intentlatch import metrics, policies
from intentlatch.chat import Piece
from intentlatch.control_agent import Verdict, Violation
from intentlatch.identity import EmployeeRecord, Identity, TokenClaims, TokenError, verify_token
from intentlatch.pipeline import Decision, PolicyResult, evaluate_prompt
from intentlatch.policies import SNAPSHOT_FIELDS, Policy, PolicySnapshot
from intentlatch.runner import (
    TOKEN_TTL_SECONDS,
    CaseResult,
    RunRequest,
    check_case,
    count,
    judge,
    mint_token,
    result_json,
    run_json,
    run_status,
    totals,
)
from intentlatch.testcases import TEST_EMPLOYEE_ID, TEST_TEAM_MODELS, TEST_TEAM_NAME, Case, load_seeds

KEY = "k" * 32
TOKEN_ID = uuid.UUID("22222222-2222-4222-8222-222222222222")
TEAM_ID = uuid.UUID("33333333-3333-4333-8333-333333333333")
NOW = datetime(2026, 10, 4, 6, 30, 15, 987654, tzinfo=UTC)
REVOKED = {"error": {"code": "token_revoked", "message": "The employee token has been revoked or replaced."}}


def record(**changes) -> EmployeeRecord:
    values = dict(
        employee_id=TEST_EMPLOYEE_ID,
        employee_name="test-runner",
        token_id=TOKEN_ID,
        revoked_at=None,
        team_id=TEAM_ID,
        team_name=TEST_TEAM_NAME,
        authorized_models=TEST_TEAM_MODELS,
    )
    return EmployeeRecord(**(values | changes))


def case(**changes) -> Case:
    values = dict(
        code="TC-X",
        prompt="Write to jan@firma.pl",
        model="corporate-a",
        run_as_employee_id=TEST_EMPLOYEE_ID,
        expected="EDIT",
        expected_policy_code="RGX-EMAIL",
        must_not_contain=["jan@firma.pl", "Kowalski"],
    )
    return Case(**(values | changes))


def entry(code: str, kind: str | None = "regex", action: str = "edit", result: str = "violated") -> PolicyResult:
    return PolicyResult(code=code, kind=kind, ai=kind is None, action=action, result=result, reasoning=None, latency_ms=1.0)


def decision(outcome: str, responsible=(), rewritten=None, status="skipped", version=7) -> dict:
    return Decision(
        outcome=outcome,
        policy_version=version,
        policy_results=[entry(code) for code in responsible],
        rewritten=rewritten,
        control_agent_status=status,
        responsible=list(responsible),
    ).model_dump()


def test_minted_token_verifies_as_the_employee_and_expires_in_a_minute():
    fresh = mint_token(record(), KEY, datetime.now(UTC))
    assert verify_token(fresh, KEY) == TokenClaims(employee_id=TEST_EMPLOYEE_ID, token_id=TOKEN_ID)
    token = mint_token(record(), KEY, NOW)
    payload = jwt.decode(token, KEY, algorithms=["HS256"], options={"verify_exp": False, "verify_iat": False})
    issued = int(NOW.replace(microsecond=0).timestamp())
    assert payload == {
        "sub": str(TEST_EMPLOYEE_ID),
        "team": TEST_TEAM_NAME,
        "models": TEST_TEAM_MODELS,
        "jti": str(TOKEN_ID),
        "iat": issued,
        "exp": issued + TOKEN_TTL_SECONDS,
    }
    assert TOKEN_TTL_SECONDS == 60


def test_an_expired_minted_token_is_rejected():
    stale = mint_token(record(), KEY, datetime.now(UTC) - timedelta(seconds=TOKEN_TTL_SECONDS + 5))
    with pytest.raises(TokenError) as raised:
        verify_token(stale, KEY)
    assert raised.value.code == "token_invalid"


def test_a_matching_block_passes_and_reports_what_fired():
    result = judge(case(expected="BLOCK", must_not_contain=[]), 200, decision("blocked", ["RGX-EMAIL", "AI-X"]), 12.34567)
    assert result == CaseResult(
        case_code="TC-X",
        expected="BLOCK",
        actual="BLOCK",
        fired_policy_codes=["RGX-EMAIL", "AI-X"],
        passed=True,
        failure=None,
        policy_version=7,
        duration_ms=12.346,
    )


def test_an_allow_case_passes_on_an_allowed_outcome():
    allow = case(expected="ALLOW", expected_policy_code=None, must_not_contain=[])
    assert judge(allow, 200, decision("allowed", status="pass"), 1.0).passed


def test_an_edit_passes_when_the_rewrite_drops_every_listed_string():
    result = judge(case(), 200, decision("edited", ["RGX-EMAIL"], "Write to [removed]", "modified"), 1.0)
    assert (result.actual, result.passed, result.failure) == ("EDIT", True, None)


@pytest.mark.parametrize(
    ("given", "failure"),
    [
        (decision("blocked", ["RGX-CLOUD-KEY"]), "Expected EDIT, got BLOCK."),
        (decision("allowed", status="pass"), "Expected EDIT, got ALLOW."),
        (decision("edited", ["RGX-PHONE"], "x", "modified"), "Expected RGX-EMAIL to fire; fired: RGX-PHONE."),
        (decision("edited", [], "x", "modified"), "Expected RGX-EMAIL to fire; fired: none."),
        (
            decision("edited", ["RGX-EMAIL"], "Write to [removed], KOWALSKI", "modified"),
            "The rewritten prompt still contains must_not_contain entry 2.",
        ),
        (
            decision("edited", ["RGX-EMAIL"], "Write to [removed]", "error"),
            "The control agent failed, so the policies were not fully checked.",
        ),
    ],
)
def test_the_first_failing_rule_is_reported(given, failure):
    result = judge(case(), 200, given, 1.0)
    assert (result.passed, result.failure) == (False, failure)
    assert result.actual != "ERROR"


def test_an_agent_failure_fails_even_when_the_outcome_matches():
    allow = case(expected="ALLOW", expected_policy_code=None, must_not_contain=[])
    result = judge(allow, 200, decision("allowed", status="error"), 1.0)
    assert (result.actual, result.passed) == ("ALLOW", False)


@pytest.mark.parametrize(
    ("status", "body", "failure"),
    [
        (401, REVOKED, "The check answered 401 token_revoked: The employee token has been revoked or replaced."),
        (503, {"error": {"code": "database_unavailable", "message": "The database is unavailable."}},
         "The check answered 503 database_unavailable: The database is unavailable."),
        (200, None, "The check answered 200 without a decision."),
        (200, {"outcome": "maybe"}, "The check answered 200 without a decision."),
        (500, "oops", "The check answered 500 without a decision."),
    ],
)
def test_an_answer_without_a_decision_is_an_error(status, body, failure):
    result = judge(case(), status, body, 2.0)
    assert (result.actual, result.passed, result.failure) == ("ERROR", False, failure)
    assert (result.fired_policy_codes, result.policy_version, result.duration_ms) == ([], None, 2.0)


def test_error_text_is_bounded_and_odd_fields_are_ignored():
    long = {"error": {"code": "x", "message": "m" * 1000}}
    assert len(judge(case(), 400, long, 0.0).failure) == 300
    odd = decision("blocked", ["RGX-EMAIL"]) | {"responsible": ["RGX-EMAIL", 3], "policy_version": True}
    result = judge(case(expected="BLOCK", must_not_contain=[]), 200, odd, 0.0)
    assert (result.fired_policy_codes, result.policy_version, result.passed) == (["RGX-EMAIL"], None, True)
    assert judge(case(), 200, decision("blocked") | {"responsible": "RGX-EMAIL"}, 0.0).fired_policy_codes == []


async def check(handler) -> CaseResult:
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://intentlatch.internal") as client:
        return await check_case(client, case(), "minted.token.value")


@pytest.mark.anyio
async def test_the_check_call_sends_the_case_as_the_minted_token():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=decision("edited", ["RGX-EMAIL"], "Write to [removed]", "modified"))

    result = await check(handler)
    assert result.passed
    (request,) = seen
    assert (request.method, request.url.path) == ("POST", "/check")
    assert request.headers["authorization"] == "Bearer minted.token.value"
    assert json.loads(request.content) == {"model": "corporate-a", "prompt": "Write to jan@firma.pl"}


@pytest.mark.anyio
async def test_the_check_call_maps_error_answers_and_failures():
    assert (await check(lambda request: httpx.Response(401, json=REVOKED))).failure.startswith("The check answered 401")
    assert (await check(lambda request: httpx.Response(200, text="not json"))).actual == "ERROR"

    def unreachable(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    result = await check(unreachable)
    assert (result.actual, result.failure) == ("ERROR", "The check could not run.")


def outcome(passed: bool) -> CaseResult:
    return CaseResult(
        case_code="TC-X", expected="ALLOW", actual="ALLOW", fired_policy_codes=[], passed=passed,
        failure=None if passed else "x", policy_version=1, duration_ms=1.0,
    )


def test_a_run_passes_only_when_something_ran_and_everything_passed():
    assert run_status([]) == "failed"
    assert run_status([outcome(True), outcome(True)]) == "passed"
    assert run_status([outcome(True), outcome(False)]) == "failed"
    assert totals([outcome(True), outcome(False), outcome(True)]) == {"total": 3, "passed": 2, "failed": 1}
    assert totals([]) == {"total": 0, "passed": 0, "failed": 0}


def test_each_result_is_counted_by_its_outcome():
    def value(result: str) -> float:
        return metrics.REGISTRY.get_sample_value("intentlatch_test_cases_total", {"result": result}) or 0.0

    passed, failed = value("passed"), value("failed")
    count(outcome(True))
    count(outcome(False))
    count(outcome(False))
    assert (value("passed") - passed, value("failed") - failed) == (1, 2)


def test_run_request_defaults_and_normalizes_codes():
    request = RunRequest()
    assert (request.cases, request.mode, request.started_by) == (None, "check", "api")
    request = RunRequest(cases=[" tc-a", "TC-B", "tc-a"], started_by="  cli ")
    assert (request.cases, request.started_by) == (["TC-A", "TC-B"], "cli")


@pytest.mark.parametrize(
    "body",
    [
        {"cases": []},
        {"cases": ["not a code"]},
        {"cases": "TC-A"},
        {"mode": "full"},
        {"started_by": ""},
        {"started_by": "x" * 65},
        {"started_by": "a\x00"},
        {"started_by": "a\ud800"},
        {"case": ["TC-A"]},
    ],
)
def test_bad_run_requests_are_rejected(body):
    with pytest.raises(ValidationError):
        RunRequest.model_validate(body)


def test_run_and_result_rows_become_json():
    run_id = uuid.uuid4()
    stamp = datetime(2026, 10, 4, 6, 0, tzinfo=UTC)
    row = (run_id, stamp, None, "cli", "check", 7, "running", {"total": 2, "passed": 0, "failed": 0})
    assert run_json(row) == {
        "id": str(run_id),
        "started_at": "2026-10-04T06:00:00+00:00",
        "finished_at": None,
        "started_by": "cli",
        "mode": "check",
        "policy_version": 7,
        "status": "running",
        "totals": {"total": 2, "passed": 0, "failed": 0},
    }
    stored = ("TC-X", "BLOCK", "BLOCK", ("AUTH-MODEL",), True, None, 7, 1.5)
    assert result_json(stored) == outcome(True).model_dump() | {
        "expected": "BLOCK", "actual": "BLOCK", "fired_policy_codes": ["AUTH-MODEL"], "policy_version": 7, "duration_ms": 1.5,
    }


SNAPSHOT = PolicySnapshot(
    version=3,
    policies=[
        Policy(**seed.model_dump(include=set(SNAPSHOT_FIELDS))) for seed in policies.load_seeds() if seed.enabled
    ],
)
TEST_IDENTITY = Identity(
    employee_id=TEST_EMPLOYEE_ID, employee_name="test-runner", team_id=TEAM_ID, team_name=TEST_TEAM_NAME,
    authorized_models=TEST_TEAM_MODELS,
)
SEED_CASES = [Case(**seed.model_dump(exclude={"name"})) for seed in load_seeds()]


def answer(seed: Case, verdict: Verdict | None = None) -> dict:
    return evaluate_prompt(SNAPSHOT, TEST_IDENTITY, seed.model, [Piece(seed.prompt, True)], {}, verdict=verdict).model_dump()


@pytest.mark.parametrize(
    "seed", [seed for seed in SEED_CASES if seed.expected == "BLOCK" and not seed.expected_policy_code.startswith("AI-")],
    ids=lambda seed: seed.code,
)
def test_predefined_block_cases_pass_against_the_seeded_policies(seed):
    assert judge(seed, 200, answer(seed), 1.0).passed


@pytest.mark.parametrize("seed", [seed for seed in SEED_CASES if seed.expected != "BLOCK"], ids=lambda seed: seed.code)
def test_predefined_edit_and_allow_cases_pass_unless_the_agent_flags_a_block_policy(seed):
    # The gateway cuts regex matches itself, so even a rewrite that keeps the value cannot undo the edit.
    lazy = Verdict(status="modified", violations=[], rewritten=[seed.prompt])
    assert judge(seed, 200, answer(seed, lazy), 1.0).passed
    false_alarm = Verdict(status="blocked", violations=[Violation(code="AI-NO-CREDENTIALS", reasoning="Looks secret.")])
    assert judge(seed, 200, answer(seed, false_alarm), 1.0).failure == f"Expected {seed.expected}, got BLOCK."
