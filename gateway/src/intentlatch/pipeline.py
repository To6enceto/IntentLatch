import logging
import re
import time
from typing import Literal

from fastapi import Request
from pydantic import BaseModel

from . import policies
from .errors import GatewayError
from .identity import Identity
from .normalize import normalized_views
from .policies import Action, Kind, Policy, PolicySnapshot

log = logging.getLogger("intentlatch")

Result = Literal["pass", "violated", "error"]
Outcome = Literal["allowed", "edited", "blocked"]
Direction = Literal["prompt", "response"]


class PolicyResult(BaseModel):
    """One policy's verdict; the same shape the decision record stores."""

    code: str
    kind: Kind | None
    ai: bool
    action: Action
    result: Result
    reasoning: str | None
    latency_ms: float


class Decision(BaseModel):
    outcome: Outcome
    policy_version: int
    policy_results: list[PolicyResult]
    rewritten: str | None = None


def elapsed_ms(started: float) -> float:
    return round(max(time.perf_counter() - started, 0.0) * 1000, 3)


def authority_result(policy: Policy, identity: Identity, model: str) -> PolicyResult:
    # authorized_models comes from the database, never from the token or the body.
    started = time.perf_counter()
    authorized = model in identity.authorized_models
    return PolicyResult(
        code=policy.code,
        kind=policy.kind,
        ai=policy.ai,
        action=policy.action,
        result="pass" if authorized else "violated",
        reasoning=None if authorized else f"Team {identity.team_name} is not authorized for {model}.",
        latency_ms=elapsed_ms(started),
    )


def luhn_valid(digits: list[int]) -> bool:
    if len(digits) < 2:
        return False
    total = 0
    for position, digit in enumerate(reversed(digits)):
        if position % 2:
            digit = digit * 2 - 9 if digit > 4 else digit * 2
        total += digit
    return total % 10 == 0


def luhn_counted(match: re.Match[str]) -> bool:
    captured = match.group("luhn") or ""
    return luhn_valid([int(char) for char in captured if char.isdecimal()])


def pattern_matches(pattern: str, views: list[str]) -> bool:
    compiled = re.compile(pattern)
    if "luhn" not in compiled.groupindex:
        return any(compiled.search(view) for view in views)
    # A `luhn` group makes a match count only when the digits it captured pass the checksum.
    return any(luhn_counted(match) for view in views for match in compiled.finditer(view))


def regex_result(policy: Policy, views: list[str], direction: Direction) -> PolicyResult:
    started = time.perf_counter()
    violated = pattern_matches(policy.params["pattern"], views)
    return PolicyResult(
        code=policy.code,
        kind=policy.kind,
        ai=policy.ai,
        action=policy.action,
        result="violated" if violated else "pass",
        # Never the matched text: callers see the reasoning, and decision records will store it.
        reasoning=f"The {direction} matches this policy's pattern." if violated else None,
        latency_ms=elapsed_ms(started),
    )


def regex_results(
    snapshot: PolicySnapshot, texts: list[str], direction: Direction
) -> list[PolicyResult]:
    views = [view for text in texts for view in normalized_views(text)]
    return [
        regex_result(policy, views, direction)
        for policy in snapshot.policies
        if policy.kind == "regex" and policy.applies_to in (direction, "both")
    ]


def blocking(results: list[PolicyResult]) -> list[PolicyResult]:
    return [entry for entry in results if entry.result == "violated" and entry.action == "block"]


def responsible(results: list[PolicyResult]) -> list[PolicyResult]:
    # Violated edit policies answer for a block only when no block policy was violated.
    violated = [entry for entry in results if entry.result == "violated"]
    return blocking(violated) or violated


def decide(results: list[PolicyResult], version: int) -> Decision:
    # Interim until the control agent (item 9) can rewrite: a violated edit policy blocks too.
    blocked = any(entry.result == "violated" for entry in results)
    return Decision(
        outcome="blocked" if blocked else "allowed", policy_version=version, policy_results=results
    )


def evaluate_prompt(
    snapshot: PolicySnapshot, identity: Identity, model: str, texts: list[str]
) -> Decision:
    # Authority runs first (limit policies join it in item 8), then regex; the control
    # agent (item 9) follows. A stage that violates a block policy stops the pipeline.
    results = [
        authority_result(policy, identity, model)
        for policy in snapshot.policies
        if policy.kind == "authority"
    ]
    if not blocking(results):
        results += regex_results(snapshot, texts, "prompt")
    return decide(results, snapshot.version)


def evaluate_response(snapshot: PolicySnapshot, texts: list[str]) -> Decision:
    # Authority and limit policies are prompt-only, so a response has only the regex stage.
    return decide(regex_results(snapshot, texts, "response"), snapshot.version)


def blocked_error(decision: Decision) -> GatewayError:
    blocked = responsible(decision.policy_results)
    return GatewayError(
        403,
        "policy_blocked",
        "Blocked by " + "; ".join(f"{entry.code}: {entry.reasoning}" for entry in blocked),
        extra={
            "policies": [
                {"code": entry.code, "kind": entry.kind, "reasoning": entry.reasoning}
                for entry in blocked
            ],
            "policy_version": decision.policy_version,
        },
    )


def enforce(request: Request, decision: Decision, direction: Direction) -> None:
    if decision.outcome == "blocked":
        codes = ",".join(entry.code for entry in responsible(decision.policy_results))
        log.info(
            "policy blocked request_id=%s direction=%s codes=%s",
            request.state.request_id,
            direction,
            codes,
        )
        raise blocked_error(decision)


async def decide_prompt(request: Request, model: str, texts: list[str]) -> Decision:
    snapshot = await policies.load_snapshot(request.app.state.pool)
    return evaluate_prompt(snapshot, request.state.identity, model, texts)


async def enforce_prompt_policies(
    request: Request, model: str, texts: list[str]
) -> PolicySnapshot:
    # Returns the snapshot so the response is checked against the same policy version.
    snapshot = await policies.load_snapshot(request.app.state.pool)
    enforce(request, evaluate_prompt(snapshot, request.state.identity, model, texts), "prompt")
    return snapshot


def enforce_response_policies(
    request: Request, snapshot: PolicySnapshot, texts: list[str]
) -> None:
    enforce(request, evaluate_response(snapshot, texts), "response")
