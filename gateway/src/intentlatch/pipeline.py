import logging
import time
from typing import Literal

from fastapi import Request
from pydantic import BaseModel

from . import policies
from .errors import GatewayError
from .identity import Identity
from .policies import Action, Kind, Policy, PolicySnapshot

log = logging.getLogger("intentlatch")

Result = Literal["pass", "violated", "error"]
Outcome = Literal["allowed", "edited", "blocked"]


class PolicyResult(BaseModel):
    """One policy's verdict; the same shape the decision record stores."""

    code: str
    kind: Kind | None
    ai: bool
    action: Action
    result: Result
    reasoning: str | None
    latency_ms: float


class PromptDecision(BaseModel):
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


def blocking(results: list[PolicyResult]) -> list[PolicyResult]:
    return [entry for entry in results if entry.result == "violated" and entry.action == "block"]


def evaluate_prompt(snapshot: PolicySnapshot, identity: Identity, model: str) -> PromptDecision:
    # Stage 1, the non-AI prompt checks. Limit policies join this stage in item 8;
    # the regex (item 7) and control-agent (item 9) stages follow it, and every
    # stage stops the pipeline when it produces a violated block policy.
    results = [
        authority_result(policy, identity, model)
        for policy in snapshot.policies
        if policy.kind == "authority"
    ]
    outcome: Outcome = "blocked" if blocking(results) else "allowed"
    return PromptDecision(outcome=outcome, policy_version=snapshot.version, policy_results=results)


def blocked_error(decision: PromptDecision) -> GatewayError:
    blocked = blocking(decision.policy_results)
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


async def decide_prompt(request: Request, model: str) -> PromptDecision:
    snapshot = await policies.load_snapshot(request.app.state.pool)
    return evaluate_prompt(snapshot, request.state.identity, model)


async def enforce_prompt_policies(request: Request, model: str) -> PromptDecision:
    decision = await decide_prompt(request, model)
    if decision.outcome == "blocked":
        codes = ",".join(entry.code for entry in blocking(decision.policy_results))
        log.info("policy blocked request_id=%s codes=%s", request.state.request_id, codes)
        raise blocked_error(decision)
    return decision
