import argparse
import os
import sys
from collections.abc import Mapping, Sequence
from typing import Any, TextIO

import httpx

from . import console_cli

# The gateway's port in its container and in development.
DEFAULT_URL = "http://127.0.0.1:8080"
ADMIN_KEY_VAR = "INTENTLATCH_ADMIN_API_KEY"
CONNECT_TIMEOUT_SECONDS = 5
EXIT_PASSED, EXIT_FAILED, EXIT_ERROR = 0, 1, 2


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="intentlatch", description="IntentLatch gateway tools.")
    commands = root.add_subparsers(dest="command", required=True)
    test = commands.add_parser("test", help="policy test cases")
    actions = test.add_subparsers(dest="action", required=True)
    run = actions.add_parser("run", help="run test cases through the gateway's check endpoint")
    run.add_argument("--case", dest="cases", action="append", metavar="CODE", help="run only this case; repeat for more")
    run.add_argument("--url", default=DEFAULT_URL, help=f"the gateway's base URL (default {DEFAULT_URL})")
    console_cli.add_parser(commands)
    return root


def result_line(result: dict[str, Any]) -> str:
    fired = ",".join(result.get("fired_policy_codes") or []) or "-"
    line = (
        f"{'PASS' if result.get('passed') else 'FAIL'}  {result.get('case_code')}"
        f"  {result.get('expected')} -> {result.get('actual')}  fired {fired}  {result.get('duration_ms', 0):.0f} ms"
    )
    failure = result.get("failure")
    return f"{line}  {failure}" if failure else line


def run_lines(run: dict[str, Any]) -> list[str]:
    totals = run.get("totals") or {}
    return [
        f"Test run {run.get('id')}: {run.get('mode')} mode, policy version {run.get('policy_version')}",
        *(result_line(result) for result in run.get("results") or []),
        f"{totals.get('passed', 0)} passed, {totals.get('failed', 0)} failed,"
        f" {totals.get('total', 0)} total: {run.get('status')}",
    ]


def error_text(response: httpx.Response) -> str:
    try:
        error = response.json().get("error")
    except (ValueError, AttributeError):
        error = None
    if isinstance(error, dict):
        return f"{response.status_code} {error.get('code')}: {error.get('message')}"
    return f"HTTP {response.status_code}"


def run_tests(
    args: argparse.Namespace, key: str, transport: httpx.BaseTransport | None, out: TextIO, err: TextIO
) -> int:
    body: dict[str, Any] = {"started_by": "cli"} | ({"cases": args.cases} if args.cases else {})
    # A run takes as long as its cases, seconds each when they reach the control agent, so reads never time out.
    timeout = httpx.Timeout(None, connect=CONNECT_TIMEOUT_SECONDS)
    try:
        with httpx.Client(base_url=args.url, timeout=timeout, transport=transport) as client:
            response = client.post("/admin/test-runs", json=body, headers={"Authorization": f"Bearer {key}"})
    except httpx.HTTPError as exc:
        print(f"intentlatch: cannot reach the gateway at {args.url} ({exc.__class__.__name__})", file=err)
        return EXIT_ERROR
    if response.status_code != 201:
        print(f"intentlatch: the test run did not start: {error_text(response)}", file=err)
        return EXIT_ERROR
    try:
        run = response.json()["test_run"]
        lines = run_lines(run)
    except (ValueError, KeyError, TypeError, AttributeError):
        print("intentlatch: the gateway answered without a test run", file=err)
        return EXIT_ERROR
    print("\n".join(lines), file=out)
    return EXIT_PASSED if run.get("status") == "passed" else EXIT_FAILED


def main(
    argv: Sequence[str] | None = None,
    environ: Mapping[str, str] = os.environ,
    transport: httpx.BaseTransport | None = None,
    out: TextIO | None = None,
    err: TextIO | None = None,
) -> int:
    """`intentlatch test run`: 0 when every case passed, 1 when any failed, 2 when the run could not happen."""
    args = parser().parse_args(argv)
    if args.command == "console-user":
        return console_cli.run(args, environ)
    err = err or sys.stderr
    key = environ.get(ADMIN_KEY_VAR)
    if not key:
        print(f"intentlatch: set {ADMIN_KEY_VAR} to the gateway's admin API key", file=err)
        return EXIT_ERROR
    return run_tests(args, key, transport, out or sys.stdout, err)


def run() -> None:
    sys.exit(main())
