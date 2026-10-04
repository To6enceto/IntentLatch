from collections import Counter as Tally
from typing import TYPE_CHECKING, Any

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram, generate_latest

from .trace import Stage, Trace

if TYPE_CHECKING:
    from .limits import WindowUsage
    from .policies import Policy, PolicySnapshot

# The classic text format: every Prometheus 2.x and 3.x server reads it.
CONTENT_TYPE = "text/plain; version=0.0.4; charset=utf-8"
# Control-agent and upstream calls take seconds on CPU, so the buckets reach two minutes.
BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 20, 30, 60, 120)
KINDS = ("authority", "limit", "regex", "ai")

REGISTRY = CollectorRegistry()


def counter(name: str, documentation: str, labels: list[str]) -> Counter:
    return Counter(name, documentation, labels, registry=REGISTRY)


REQUESTS = counter("intentlatch_requests_total", "Requests by outcome.", ["team", "model", "outcome"])
ENFORCEMENTS = counter(
    "intentlatch_policy_enforcements_total",
    "The policies responsible for each block or edit.",
    ["policy_code", "kind", "action", "outcome", "direction", "team", "model"],
)
CHECKS = counter("intentlatch_policy_checks_total", "Every policy evaluation.", ["policy_code", "kind", "result"])
CHECK_DURATION = Histogram(
    "intentlatch_policy_check_duration_seconds", "Cost of each policy kind.", ["kind"], buckets=BUCKETS, registry=REGISTRY
)
REQUEST_DURATION = Histogram(
    "intentlatch_request_duration_seconds", "Time per request stage.", ["stage"], buckets=BUCKETS, registry=REGISTRY
)
TOKENS = counter("intentlatch_tokens_total", "Prompt and completion tokens.", ["team", "model", "direction"])
COMPUTE = counter("intentlatch_compute_seconds_total", "Local model time.", ["team", "model"])
USAGE_RATIO = Gauge(
    "intentlatch_team_token_usage_ratio",
    "Usage against the team's strictest limit in the current window.",
    ["team"],
    registry=REGISTRY,
)
AUTH_FAILURES = counter("intentlatch_auth_failures_total", "Rejected identity tokens and unauthorized models.", ["reason"])
UPSTREAM_ERRORS = counter("intentlatch_upstream_errors_total", "Corporate LLM failures.", ["model", "reason"])
AGENT_VERDICTS = counter("intentlatch_control_agent_verdicts_total", "Control agent verdicts.", ["direction", "status"])
AGENT_ERRORS = counter("intentlatch_control_agent_errors_total", "Control agent failures.", ["reason"])
REWRITE_REJECTIONS = counter(
    "intentlatch_rewrite_rejections_total", "Rewrites blocked because an edit regex still matched.", ["policy_code"]
)
TEST_CASES = counter("intentlatch_test_cases_total", "Test case passes and failures.", ["result"])
POLICIES_ACTIVE = Gauge("intentlatch_policies_active", "Enabled policies per kind.", ["kind"], registry=REGISTRY)


def kind_label(entry: Any) -> str:
    return entry.kind or "ai"


def request_outcome(stages: list[Stage]) -> str:
    outcomes = {stage.decision.outcome for stage in stages}
    if "blocked" in outcomes:
        return "blocked"
    return "edited" if "edited" in outcomes else "allowed"


def observe_stage(trace: Trace, stage: Stage, direction: str) -> None:
    decision = stage.decision
    by_code = {entry.code: entry for entry in decision.policy_results}
    for entry in decision.policy_results:
        CHECKS.labels(entry.code, kind_label(entry), entry.result).inc()
        CHECK_DURATION.labels(kind_label(entry)).observe(entry.latency_ms / 1000)
        if entry.kind == "authority" and entry.result == "violated":
            AUTH_FAILURES.labels("model_not_authorized").inc()
    if decision.outcome in ("blocked", "edited"):
        for code in decision.responsible:
            entry = by_code[code]
            ENFORCEMENTS.labels(
                code, kind_label(entry), entry.action, decision.outcome, direction, trace.identity.team_name, trace.model
            ).inc()
            # An edit policy responsible for a block is a rewrite that still matched.
            if decision.outcome == "blocked" and entry.action == "edit":
                REWRITE_REJECTIONS.labels(code).inc()
    if decision.control_agent_status != "skipped":
        AGENT_VERDICTS.labels(direction, decision.control_agent_status).inc()
    if decision.agent_failure is not None:
        AGENT_ERRORS.labels(decision.agent_failure).inc()


def observe(trace: Trace, total_ms: float) -> None:
    """Counts one finished chat request from its trace; labels never carry free text."""
    directions = [(name, stage) for name, stage in (("prompt", trace.prompt), ("response", trace.response)) if stage]
    if not directions:
        return
    team, model = trace.identity.team_name, trace.model
    stages = [stage for _, stage in directions]
    REQUESTS.labels(team, model, request_outcome(stages)).inc()
    for direction, stage in directions:
        observe_stage(trace, stage, direction)
    REQUEST_DURATION.labels("auth").observe(trace.auth_ms / 1000)
    REQUEST_DURATION.labels("non_ai").observe(sum(stage.non_ai_ms for stage in stages) / 1000)
    agent_ms = sum(stage.agent_ms for stage in stages)
    if agent_ms:
        REQUEST_DURATION.labels("control_agent").observe(agent_ms / 1000)
    if trace.upstream_ms:
        REQUEST_DURATION.labels("upstream").observe(trace.upstream_ms / 1000)
    REQUEST_DURATION.labels("total").observe(total_ms / 1000)
    TOKENS.labels(team, model, "prompt").inc(trace.tokens_in)
    TOKENS.labels(team, model, "response").inc(trace.tokens_out)
    COMPUTE.labels(team, model).inc(trace.upstream_seconds + sum(stage.agent_seconds for stage in stages))
    if trace.upstream_error is not None:
        UPSTREAM_ERRORS.labels(model, trace.upstream_error).inc()


def set_active_policies(snapshot: "PolicySnapshot") -> None:
    counts = Tally(policy.kind or "ai" for policy in snapshot.policies)
    for kind in KINDS:
        POLICIES_ACTIVE.labels(kind).set(counts[kind])


def set_usage_ratio(team: str, limits: list["Policy"], usage: dict[int, "WindowUsage"]) -> None:
    if not limits:
        return
    ratios = [usage[policy.params["window_seconds"]].used / policy.params["max_tokens"] for policy in limits]
    USAGE_RATIO.labels(team).set(max(ratios))


def render() -> bytes:
    return generate_latest(REGISTRY)
