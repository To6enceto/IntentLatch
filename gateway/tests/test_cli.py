import io
import json
from collections.abc import Callable

import httpx
import pytest

from intentlatch.cli import DEFAULT_URL, main, parser, run_lines

Handler = Callable[[httpx.Request], httpx.Response]
KEY = "a" * 32
ENV = {"INTENTLATCH_ADMIN_API_KEY": KEY}
PASSED = {"case_code": "TC-AUTH-MODEL", "expected": "BLOCK", "actual": "BLOCK", "fired_policy_codes": ["AUTH-MODEL"],
          "passed": True, "failure": None, "policy_version": 4, "duration_ms": 2.4}
FAILED = {"case_code": "TC-RGX-EMAIL", "expected": "EDIT", "actual": "ERROR", "fired_policy_codes": [], "passed": False,
          "failure": "The check could not run.", "policy_version": None, "duration_ms": 8123.6}


def run_body(results: list[dict], status: str) -> dict:
    passed = sum(result["passed"] for result in results)
    return {
        "id": "4b0c6f3e-0000-4000-8000-000000000009",
        "started_at": "2026-10-04T05:00:00+00:00",
        "finished_at": "2026-10-04T05:00:09+00:00",
        "started_by": "cli",
        "mode": "check",
        "policy_version": 4,
        "status": status,
        "totals": {"total": len(results), "passed": passed, "failed": len(results) - passed},
        "results": results,
    }


def invoke(argv: list[str], handler: Handler, environ=ENV) -> tuple[int, str, str, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def record(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    out, err = io.StringIO(), io.StringIO()
    code = main(argv, environ=environ, transport=httpx.MockTransport(record), out=out, err=err)
    return code, out.getvalue(), err.getvalue(), seen


def answering(status: int, body) -> Handler:
    return lambda request: httpx.Response(status, json=body)


def test_arguments_parse_with_defaults_and_repeated_cases():
    args = parser().parse_args(["test", "run"])
    assert (args.cases, args.url) == (None, DEFAULT_URL)
    args = parser().parse_args(["test", "run", "--case", "tc-a", "--case", "TC-B", "--url", "http://gw:9000"])
    assert (args.cases, args.url) == (["tc-a", "TC-B"], "http://gw:9000")


@pytest.mark.parametrize("argv", [[], ["test"], ["test", "go"], ["run"]])
def test_incomplete_commands_exit_2(argv, capsys):
    with pytest.raises(SystemExit) as caught:
        parser().parse_args(argv)
    assert caught.value.code == 2


def test_a_passed_run_exits_0_and_prints_each_case():
    code, out, err, seen = invoke(["test", "run"], answering(201, {"test_run": run_body([PASSED], "passed")}))
    assert (code, err) == (0, "")
    assert out.splitlines() == [
        "Test run 4b0c6f3e-0000-4000-8000-000000000009: check mode, policy version 4",
        "PASS  TC-AUTH-MODEL  BLOCK -> BLOCK  fired AUTH-MODEL  2 ms",
        "1 passed, 0 failed, 1 total: passed",
    ]
    (request,) = seen
    assert (request.method, str(request.url)) == ("POST", f"{DEFAULT_URL}/admin/test-runs")
    assert request.headers["authorization"] == f"Bearer {KEY}"
    assert json.loads(request.content) == {"started_by": "cli"}


def test_a_failed_run_exits_1_and_names_the_failure():
    run = run_body([PASSED, FAILED], "failed")
    code, out, _, seen = invoke(["test", "run", "--case", "TC-AUTH-MODEL", "--case", "TC-RGX-EMAIL"], answering(201, {"test_run": run}))
    assert code == 1
    assert "FAIL  TC-RGX-EMAIL  EDIT -> ERROR  fired -  8124 ms  The check could not run." in out.splitlines()
    assert out.splitlines()[-1] == "1 passed, 1 failed, 2 total: failed"
    assert json.loads(seen[0].content) == {"started_by": "cli", "cases": ["TC-AUTH-MODEL", "TC-RGX-EMAIL"]}


def test_an_empty_run_exits_1():
    code, out, _, _ = invoke(["test", "run"], answering(201, {"test_run": run_body([], "failed")}))
    assert (code, out.splitlines()[-1]) == (1, "0 passed, 0 failed, 0 total: failed")


def test_a_missing_admin_key_exits_2_before_any_request():
    code, out, err, seen = invoke(["test", "run"], answering(201, {}), environ={})
    assert (code, out, seen) == (2, "", [])
    assert "INTENTLATCH_ADMIN_API_KEY" in err


@pytest.mark.parametrize(
    ("status", "body", "message"),
    [
        (401, {"error": {"code": "admin_key_invalid", "message": "A valid admin API key is required."}},
         "401 admin_key_invalid: A valid admin API key is required."),
        (400, {"error": {"code": "invalid_request", "message": "cases: unknown test case code(s): TC-NOPE"}},
         "400 invalid_request: cases: unknown test case code(s): TC-NOPE"),
        (502, "bad gateway", "HTTP 502"),
    ],
)
def test_an_error_answer_exits_2_with_its_reason(status, body, message):
    code, out, err, _ = invoke(["test", "run", "--url", "http://gw:9000"], answering(status, body))
    assert (code, out) == (2, "")
    assert err.strip() == f"intentlatch: the test run did not start: {message}"
    assert KEY not in err


def test_an_unreachable_gateway_exits_2():
    def refused(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    code, _, err, _ = invoke(["test", "run", "--url", "http://gw:9000"], refused)
    assert code == 2
    assert err.strip() == "intentlatch: cannot reach the gateway at http://gw:9000 (ConnectError)"


@pytest.mark.parametrize("body", [{}, {"test_run": None}, ["x"]])
def test_an_answer_without_a_run_exits_2(body):
    code, _, err, _ = invoke(["test", "run"], answering(201, body))
    assert (code, err.strip()) == (2, "intentlatch: the gateway answered without a test run")


def test_run_lines_tolerate_missing_fields():
    assert run_lines({}) == ["Test run None: None mode, policy version None", "0 passed, 0 failed, 0 total: None"]
