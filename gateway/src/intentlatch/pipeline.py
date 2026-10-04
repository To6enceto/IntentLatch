import logging
import re
import time
from typing import Literal

from fastapi import Request
from pydantic import BaseModel

from . import limits, policies
from .errors import GatewayError
from .identity import Identity
from .limits import WindowUsage
from .normalize import normalized_views
from .policies import Action, Kind, Policy, PolicySnapshot
from .teams import iso

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


def team_limits(snapshot: PolicySnapshot, identity: Identity) -> list[Policy]:
    # A null team means every team, each against its own usage.
    return [
        policy
        for policy in snapshot.policies
        if policy.kind == "limit" and policy.params["team"] in (None, identity.team_name)
    ]


def limit_result(policy: Policy, identity: Identity, usage: dict[int, WindowUsage]) -> PolicyResult:
    started = time.perf_counter()
    max_tokens = policy.params["max_tokens"]
    window_seconds = policy.params["window_seconds"]
    # Usage never loaded for this window is a bug: the KeyError fails the request, never passes it.
    window = usage[window_seconds]
    violated = window.used >= max_tokens
    return PolicyResult(
        code=policy.code,
        kind=policy.kind,
        ai=policy.ai,
        action=policy.action,
        result="violated" if violated else "pass",
        reasoning=(
            f"Team {identity.team_name} has used {window.used} of {max_tokens} tokens"
            f" in the {window_seconds}-second window ending {iso(window.ends)}."
            if violated
            else None
        ),
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
    snapshot: PolicySnapshot,
    identity: Identity,
    model: str,
    texts: list[str],
    usage: dict[int, WindowUsage],
) -> Decision:
    # Authority and limit policies form the first stage, then regex; the control agent
    # (item 9) follows. A stage that violates a block policy stops the pipeline.
    results = [
        authority_result(policy, identity, model)
        for policy in snapshot.policies
        if policy.kind == "authority"
    ]
    results += [limit_result(policy, identity, usage) for policy in team_limits(snapshot, identity)]
    if not blocking(results):
        results += regex_results(snapshot, texts, "prompt")
    return decide(results, snapshot.version)


def evaluate_response(snapshot: PolicySnapshot, texts: list[str]) -> Decision:
    # Authority and limit policies are prompt-only, so a response has only the regex stage.
    return decide(regex_results(snapshot, texts, "response"), snapshot.version)


def blocked_error(decision: Decision) -> GatewayError:
    blocked = responsible(decision.policy_results)
    # A block by limits alone lifts when their windows end, so it is 429; any other block is final.
    limited = all(entry.kind == "limit" for entry in blocked)
    return GatewayError(
        429 if limited else 403,
        "limit_exceeded" if limited else "policy_blocked",
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


async def prompt_decision(
    request: Request, model: str, texts: list[str]
) -> tuple[PolicySnapshot, Decision]:
    pool = request.app.state.pool
    identity = request.state.identity
    snapshot = await policies.load_snapshot(pool)
    windows = {policy.params["window_seconds"] for policy in team_limits(snapshot, identity)}
    usage = await limits.load_usage(pool, identity.team_id, windows)
    return snapshot, evaluate_prompt(snapshot, identity, model, texts, usage)


async def decide_prompt(request: Request, model: str, texts: list[str]) -> Decision:
    _, decision = await prompt_decision(request, model, texts)
    return decision


async def enforce_prompt_policies(
    request: Request, model: str, texts: list[str]
) -> PolicySnapshot:
    # Returns the snapshot so the response is checked against the same policy version.
    snapshot, decision = await prompt_decision(request, model, texts)
    enforce(request, decision, "prompt")
    return snapshot


def enforce_response_policies(
    request: Request, snapshot: PolicySnapshot, texts: list[str]
) -> None:
    enforce(request, evaluate_response(snapshot, texts), "response")
