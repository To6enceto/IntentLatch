import logging
import re
import time
from typing import Literal

from fastapi import Request
from pydantic import BaseModel, Field

from . import control_agent, limits, metrics, policies
from .chat import Piece
from .control_agent import FAILURE_TEXT, AgentFailure, FailureReason, Verdict
from .errors import GatewayError
from .identity import Identity
from .limits import WindowUsage
from .normalize import normalized_views
from .policies import Action, Kind, Policy, PolicySnapshot
from .teams import iso
from .trace import Stage

log = logging.getLogger("intentlatch")

REDACTED = "[removed]"

Result = Literal["pass", "violated", "error"]
Outcome = Literal["allowed", "edited", "blocked"]
Direction = Literal["prompt", "response"]
AgentStatus = Literal["skipped", "pass", "blocked", "modified", "error"]


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
    control_agent_status: AgentStatus = "skipped"
    responsible: list[str] = Field(default_factory=list)
    # Internal: the final text of every piece when edited, and why a fail-closed agent blocked.
    rewrites: list[str] | None = Field(default=None, exclude=True)
    agent_failure: FailureReason | None = Field(default=None, exclude=True)


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


def matched_span(match: re.Match[str]) -> tuple[int, int]:
    # A lookahead pattern matches no text, so it tries every start position; its luhn group holds the value.
    if match.start() == match.end() and "luhn" in match.re.groupindex and match.group("luhn") is not None:
        return match.span("luhn")
    return match.span()


def pattern_matches(pattern: str, views: list[str]) -> bool:
    compiled = re.compile(pattern)
    if "luhn" not in compiled.groupindex:
        return any(compiled.search(view) for view in views)
    # A `luhn` group makes a match count only when the digits it captured pass the checksum.
    return any(luhn_counted(match) for view in views for match in compiled.finditer(view))


def counted_spans(compiled: re.Pattern[str], text: str) -> list[tuple[int, int]]:
    """The spans of checksum-valid captures, merged where they touch or overlap."""
    merged: list[tuple[int, int]] = []
    for start, end in sorted(matched_span(match) for match in compiled.finditer(text) if luhn_counted(match)):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def replace_spans(text: str, spans: list[tuple[int, int]], placeholder: str) -> str:
    pieces, last = [], 0
    for start, end in spans:
        pieces += [text[last:start], placeholder]
        last = end
    return "".join(pieces) + text[last:]


def replace_matches(text: str, pattern: str, placeholder: str) -> str:
    compiled = re.compile(pattern)
    if "luhn" in compiled.groupindex:
        return replace_spans(text, counted_spans(compiled, text), placeholder)
    return compiled.sub(lambda match: placeholder, text)


def text_views(texts: list[str]) -> list[str]:
    return [view for text in texts for view in normalized_views(text)]


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
    views = text_views(texts)
    return [
        regex_result(policy, views, direction)
        for policy in snapshot.policies
        if policy.kind == "regex" and policy.applies_to in (direction, "both")
    ]


def blocking(results: list[PolicyResult]) -> list[PolicyResult]:
    return [entry for entry in results if entry.result == "violated" and entry.action == "block"]


def violated_edits(results: list[PolicyResult]) -> list[PolicyResult]:
    return [entry for entry in results if entry.result == "violated" and entry.action == "edit"]


def codes(results: list[PolicyResult]) -> list[str]:
    return [entry.code for entry in results]


def applying_ai_policies(snapshot: PolicySnapshot, direction: Direction) -> list[Policy]:
    return [policy for policy in snapshot.policies if policy.ai and policy.applies_to in (direction, "both")]


def needs_agent(snapshot: PolicySnapshot, results: list[PolicyResult], direction: Direction) -> bool:
    # A block ends the request before the agent, and the gateway cuts edit regex matches itself.
    if blocking(results):
        return False
    return bool(applying_ai_policies(snapshot, direction))


def redact(
    snapshot: PolicySnapshot, results: list[PolicyResult], pieces: list[Piece], placeholder: str = REDACTED
) -> list[str]:
    """Each piece with every violated edit regex policy's matches replaced; tool text stays as it is."""
    # Done by the gateway, not the control agent: a small model rewrites unreliably, and it then
    # judges AI policies on text that no longer holds the personal data it tends to over-flag.
    edited = {entry.code for entry in violated_edits(results) if entry.kind == "regex"}
    patterns = [policy.params["pattern"] for policy in snapshot.policies if policy.code in edited]
    texts = []
    for piece in pieces:
        text = piece.text
        for pattern in patterns if piece.rewritable else []:
            text = replace_matches(text, pattern, placeholder)
        texts.append(text)
    return texts


def ai_results(
    policies: list[Policy], verdict: Verdict | None, failure: FailureReason | None, latency_ms: float
) -> list[PolicyResult]:
    named: dict[str, str] = {}
    for violation in verdict.violations if verdict is not None else []:
        named.setdefault(violation.code, violation.reasoning)
    entries = []
    for policy in policies:
        result: Result = "pass"
        reasoning = None
        if verdict is None:
            result, reasoning = "error", f"The control agent failed: {FAILURE_TEXT[failure]}."
        elif policy.code in named:
            result, reasoning = "violated", named[policy.code] or "The control agent found a violation."
        entries.append(
            PolicyResult(
                code=policy.code,
                kind=None,
                ai=True,
                action=policy.action,
                result=result,
                reasoning=reasoning,
                latency_ms=latency_ms,
            )
        )
    return entries


def recheck(
    snapshot: PolicySnapshot, edits: list[PolicyResult], texts: list[str]
) -> list[str]:
    edited = {entry.code for entry in edits if entry.kind == "regex"}
    views = text_views(texts)
    return [
        policy.code
        for policy in snapshot.policies
        if policy.code in edited and pattern_matches(policy.params["pattern"], views)
    ]


def settle(
    snapshot: PolicySnapshot,
    results: list[PolicyResult],
    pieces: list[Piece],
    texts: list[str],
    direction: Direction,
    status: AgentStatus,
) -> Decision:
    """Applies the edits when nothing blocks; a matched edit regex left anywhere still blocks."""
    version = snapshot.version
    edits = violated_edits(results)
    if not edits:
        return Decision(outcome="allowed", policy_version=version, policy_results=results, control_agent_status=status)
    # Tool text keeps its original form, so an edit match left there blocks below.
    final = [text if piece.rewritable else piece.text for piece, text in zip(pieces, texts, strict=True)]
    remaining = recheck(snapshot, edits, final)
    if remaining:
        still = f"The rewritten {direction} still matches this policy's pattern."
        results = [entry.model_copy(update={"reasoning": still}) if entry.code in remaining else entry for entry in results]
        return Decision(
            outcome="blocked", policy_version=version, policy_results=results,
            control_agent_status=status, responsible=remaining,
        )
    return Decision(
        outcome="edited", policy_version=version, policy_results=results, rewritten="\n\n".join(final),
        control_agent_status=status, responsible=codes(edits), rewrites=final,
    )


def conclude(
    snapshot: PolicySnapshot,
    results: list[PolicyResult],
    pieces: list[Piece],
    direction: Direction,
    verdict: Verdict | None,
    failure: FailureReason | None,
    latency_ms: float,
    production: bool,
) -> Decision:
    """Applies the blocking rule after the control agent: database actions decide, not its status."""
    version = snapshot.version
    results = results + ai_results(applying_ai_policies(snapshot, direction), verdict, failure, latency_ms)
    status: AgentStatus = verdict.status if verdict is not None else "error"
    blocked = blocking(results)
    if blocked:
        return Decision(
            outcome="blocked", policy_version=version, policy_results=results,
            control_agent_status=status, responsible=codes(blocked),
        )
    ai_edits = [entry for entry in violated_edits(results) if entry.ai]
    if verdict is not None and ai_edits and verdict.rewritten is None:
        failure, status = "missing_rewrite", "error"
    if failure is not None:
        # Fail closed in production; development lets the text through unchanged.
        return Decision(
            outcome="blocked" if production else "allowed", policy_version=version, policy_results=results,
            control_agent_status="error", agent_failure=failure,
        )
    # The agent saw the redacted pieces, so its rewrite never brings a regex match back.
    texts = verdict.rewritten if ai_edits else redact(snapshot, results, pieces)
    return settle(snapshot, results, pieces, texts, direction, status)


def finish(
    snapshot: PolicySnapshot,
    results: list[PolicyResult],
    pieces: list[Piece],
    direction: Direction,
    verdict: Verdict | None = None,
    failure: FailureReason | None = None,
    latency_ms: float = 0.0,
    production: bool = True,
) -> Decision:
    blocked = blocking(results)
    if blocked:
        return Decision(
            outcome="blocked", policy_version=snapshot.version, policy_results=results, responsible=codes(blocked)
        )
    if not needs_agent(snapshot, results, direction):
        return settle(snapshot, results, pieces, redact(snapshot, results, pieces), direction, "skipped")
    if verdict is None and failure is None:
        raise ValueError("the control agent's verdict or failure is required")
    return conclude(snapshot, results, pieces, direction, verdict, failure, latency_ms, production)


def first_stages(
    snapshot: PolicySnapshot,
    identity: Identity,
    model: str,
    texts: list[str],
    usage: dict[int, WindowUsage],
) -> list[PolicyResult]:
    # Authority and limit policies form the first stage, then regex. A stage that
    # violates a block policy stops the pipeline before the next one.
    results = [
        authority_result(policy, identity, model)
        for policy in snapshot.policies
        if policy.kind == "authority"
    ]
    results += [limit_result(policy, identity, usage) for policy in team_limits(snapshot, identity)]
    if not blocking(results):
        results += regex_results(snapshot, texts, "prompt")
    return results


def evaluate_prompt(
    snapshot: PolicySnapshot,
    identity: Identity,
    model: str,
    pieces: list[Piece],
    usage: dict[int, WindowUsage],
    verdict: Verdict | None = None,
    failure: FailureReason | None = None,
    production: bool = True,
) -> Decision:
    results = first_stages(snapshot, identity, model, [piece.text for piece in pieces], usage)
    return finish(snapshot, results, pieces, "prompt", verdict, failure, production=production)


def evaluate_response(
    snapshot: PolicySnapshot,
    pieces: list[Piece],
    verdict: Verdict | None = None,
    failure: FailureReason | None = None,
    production: bool = True,
) -> Decision:
    # Authority and limit policies are prompt-only, so a response starts at the regex stage.
    results = regex_results(snapshot, [piece.text for piece in pieces], "response")
    return finish(snapshot, results, pieces, "response", verdict, failure, production=production)


def blocked_error(decision: Decision) -> GatewayError:
    blocked = [entry for entry in decision.policy_results if entry.code in decision.responsible]
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


def agent_failed_error(decision: Decision, direction: Direction) -> GatewayError:
    # No policy was violated, so this is an unavailable check rather than a policy block.
    return GatewayError(
        503,
        "control_agent_failed",
        f"The control agent could not check this {direction}"
        f" ({FAILURE_TEXT[decision.agent_failure]}), so it was blocked.",
        extra={"policy_version": decision.policy_version},
    )


def enforce(request: Request, decision: Decision, direction: Direction) -> None:
    if decision.outcome != "blocked":
        return
    log.info(
        "policy blocked request_id=%s direction=%s codes=%s",
        request.state.request_id,
        direction,
        ",".join(decision.responsible) or "control-agent",
    )
    if decision.agent_failure is not None:
        raise agent_failed_error(decision, direction)
    raise blocked_error(decision)


async def judged(
    request: Request,
    snapshot: PolicySnapshot,
    results: list[PolicyResult],
    pieces: list[Piece],
    direction: Direction,
    non_ai_ms: float,
) -> Decision:
    production = request.app.state.settings.environment == "production"
    texts = [piece.text for piece in pieces]
    if not needs_agent(snapshot, results, direction):
        decision = finish(snapshot, results, pieces, direction, production=production)
        record_stage(request, direction, Stage(decision, texts, non_ai_ms))
        return decision
    body = control_agent.agent_request(
        direction,
        request.state.identity,
        applying_ai_policies(snapshot, direction),
        # Cut without a marker: the small model reads [removed] as a hidden credential.
        redact(snapshot, results, pieces, placeholder=""),
    )
    started = time.perf_counter()
    verdict, failure, seconds = None, None, 0.0
    try:
        verdict, seconds = await control_agent.judge(
            request.app.state.control_agent, request.app.state.settings.control_agent_model, body
        )
    except AgentFailure as exc:
        failure = exc.reason
        log.warning(
            "control agent failed request_id=%s direction=%s reason=%s",
            request.state.request_id,
            direction,
            failure,
        )
    agent_ms = elapsed_ms(started)
    decision = finish(snapshot, results, pieces, direction, verdict, failure, agent_ms, production)
    record_stage(request, direction, Stage(decision, texts, non_ai_ms, agent_ms, seconds))
    return decision


def record_stage(request: Request, direction: Direction, stage: Stage) -> None:
    # Only chat requests carry a trace; /check runs the same checks and logs nothing.
    trace = getattr(request.state, "trace", None)
    if trace is not None:
        setattr(trace, direction, stage)


async def prompt_decision(
    request: Request, model: str, pieces: list[Piece]
) -> tuple[PolicySnapshot, Decision]:
    started = time.perf_counter()
    pool = request.app.state.pool
    identity = request.state.identity
    snapshot = await policies.load_snapshot(pool)
    metrics.set_active_policies(snapshot)
    trace = getattr(request.state, "trace", None)
    if trace is not None:
        trace.snapshot = snapshot
    applying = team_limits(snapshot, identity)
    usage = await limits.load_usage(pool, identity.team_id, {policy.params["window_seconds"] for policy in applying})
    metrics.set_usage_ratio(identity.team_name, applying, usage)
    results = first_stages(snapshot, identity, model, [piece.text for piece in pieces], usage)
    return snapshot, await judged(request, snapshot, results, pieces, "prompt", elapsed_ms(started))


async def decide_prompt(request: Request, model: str, pieces: list[Piece]) -> Decision:
    _, decision = await prompt_decision(request, model, pieces)
    return decision


async def enforce_prompt_policies(
    request: Request, model: str, pieces: list[Piece]
) -> tuple[PolicySnapshot, Decision]:
    # Returns the snapshot so the response is checked against the same policy version.
    snapshot, decision = await prompt_decision(request, model, pieces)
    enforce(request, decision, "prompt")
    return snapshot, decision


async def enforce_response_policies(
    request: Request, snapshot: PolicySnapshot, pieces: list[Piece]
) -> Decision:
    started = time.perf_counter()
    results = regex_results(snapshot, [piece.text for piece in pieces], "response")
    decision = await judged(request, snapshot, results, pieces, "response", elapsed_ms(started))
    enforce(request, decision, "response")
    return decision
