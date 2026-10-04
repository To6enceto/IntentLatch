import uuid
from datetime import UTC, datetime

import pytest

from intentlatch import metrics
from intentlatch.auth import AUTH_FAILURE_REASONS, TOKEN_MESSAGES
from intentlatch.identity import Identity
from intentlatch.limits import WindowUsage
from intentlatch.pipeline import Decision, PolicyResult
from intentlatch.policies import Policy, PolicySnapshot
from intentlatch.trace import Stage, Trace

METRIC_NAMES = [
    "intentlatch_requests_total",
    "intentlatch_policy_enforcements_total",
    "intentlatch_policy_checks_total",
    "intentlatch_policy_check_duration_seconds",
    "intentlatch_request_duration_seconds",
    "intentlatch_tokens_total",
    "intentlatch_compute_seconds_total",
    "intentlatch_team_token_usage_ratio",
    "intentlatch_auth_failures_total",
    "intentlatch_upstream_errors_total",
    "intentlatch_control_agent_verdicts_total",
    "intentlatch_control_agent_errors_total",
    "intentlatch_rewrite_rejections_total",
    "intentlatch_test_cases_total",
    "intentlatch_policies_active",
]


def value(name: str, **labels: str) -> float:
    return metrics.REGISTRY.get_sample_value(name, labels) or 0.0


def result(code: str, kind: str | None, action: str, outcome: str = "pass", latency_ms: float = 2.0) -> PolicyResult:
    return PolicyResult(
        code=code, kind=kind, ai=kind is None, action=action, result=outcome, reasoning=None, latency_ms=latency_ms
    )


def decision(outcome: str, results: list[PolicyResult], responsible=(), status="skipped", failure=None) -> Decision:
    return Decision(
        outcome=outcome,
        policy_version=1,
        policy_results=results,
        responsible=list(responsible),
        control_agent_status=status,
        agent_failure=failure,
    )


def trace(team: str, prompt: Decision, response: Decision | None = None, **changes) -> Trace:
    identity = Identity(
        employee_id=uuid.uuid4(), employee_name="Ana", team_id=uuid.uuid4(), team_name=team, authorized_models=["corporate-a"]
    )
    fields = {
        "request_id": str(uuid.uuid4()),
        "identity": identity,
        "model": "corporate-a",
        "auth_ms": 3.0,
        "prompt": Stage(prompt, ["x"], non_ai_ms=4.0, agent_ms=5000.0, agent_seconds=4.5),
        "response": Stage(response, ["y"], non_ai_ms=1.0) if response is not None else None,
        "upstream_ms": 6000.0,
        "upstream_seconds": 5.5,
        "tokens_in": 31,
        "tokens_out": 12,
    }
    return Trace(**(fields | changes))


def test_an_allowed_request_counts_outcome_tokens_compute_checks_and_stages():
    stage_count = value("intentlatch_request_duration_seconds_count", stage="total")
    agent_sum = value("intentlatch_request_duration_seconds_sum", stage="control_agent")
    prompt = decision("allowed", [result("AUTH-MODEL", "authority", "block"), result("AI-X", None, "block")], status="pass")
    metrics.observe(trace("T-ALLOWED", prompt, decision("allowed", [])), total_ms=12000.0)
    assert value("intentlatch_requests_total", team="T-ALLOWED", model="corporate-a", outcome="allowed") == 1
    assert value("intentlatch_tokens_total", team="T-ALLOWED", model="corporate-a", direction="prompt") == 31
    assert value("intentlatch_tokens_total", team="T-ALLOWED", model="corporate-a", direction="response") == 12
    assert value("intentlatch_compute_seconds_total", team="T-ALLOWED", model="corporate-a") == 10.0
    assert value("intentlatch_request_duration_seconds_count", stage="total") == stage_count + 1
    assert value("intentlatch_request_duration_seconds_sum", stage="control_agent") == pytest.approx(agent_sum + 5.0)
    assert value("intentlatch_policy_checks_total", policy_code="AI-X", kind="ai", result="pass") >= 1
    assert value("intentlatch_control_agent_verdicts_total", direction="prompt", status="pass") >= 1


def test_an_edited_prompt_counts_each_responsible_edit_policy():
    before = value(
        "intentlatch_policy_enforcements_total", policy_code="RGX-MAIL", kind="regex", action="edit",
        outcome="edited", direction="prompt", team="T-EDIT", model="corporate-a",
    )
    prompt = decision(
        "edited", [result("RGX-MAIL", "regex", "edit", "violated"), result("RGX-KEY", "regex", "block")],
        responsible=["RGX-MAIL"], status="modified",
    )
    metrics.observe(trace("T-EDIT", prompt, decision("allowed", [])), total_ms=1.0)
    assert value("intentlatch_requests_total", team="T-EDIT", model="corporate-a", outcome="edited") == 1
    assert value(
        "intentlatch_policy_enforcements_total", policy_code="RGX-MAIL", kind="regex", action="edit",
        outcome="edited", direction="prompt", team="T-EDIT", model="corporate-a",
    ) == before + 1
    assert value(
        "intentlatch_policy_enforcements_total", policy_code="RGX-KEY", kind="regex", action="block",
        outcome="edited", direction="prompt", team="T-EDIT", model="corporate-a",
    ) == 0


def test_a_blocked_response_makes_the_request_blocked_and_names_only_block_policies():
    response = decision(
        "blocked",
        [result("AI-B", None, "block", "violated"), result("RGX-MAIL", "regex", "edit", "violated")],
        responsible=["AI-B"], status="blocked",
    )
    metrics.observe(trace("T-BLOCK", decision("allowed", []), response), total_ms=1.0)
    assert value("intentlatch_requests_total", team="T-BLOCK", model="corporate-a", outcome="blocked") == 1
    labels = {"outcome": "blocked", "direction": "response", "team": "T-BLOCK", "model": "corporate-a"}
    assert value("intentlatch_policy_enforcements_total", policy_code="AI-B", kind="ai", action="block", **labels) == 1
    assert value("intentlatch_policy_enforcements_total", policy_code="RGX-MAIL", kind="regex", action="edit", **labels) == 0


def test_a_rejected_rewrite_counts_as_an_edit_policy_blocking():
    before = value("intentlatch_rewrite_rejections_total", policy_code="RGX-REJECTED")
    prompt = decision("blocked", [result("RGX-REJECTED", "regex", "edit", "violated")], ["RGX-REJECTED"], "modified")
    metrics.observe(trace("T-REJECT", prompt), total_ms=1.0)
    assert value("intentlatch_rewrite_rejections_total", policy_code="RGX-REJECTED") == before + 1
    assert value(
        "intentlatch_policy_enforcements_total", policy_code="RGX-REJECTED", kind="regex", action="edit",
        outcome="blocked", direction="prompt", team="T-REJECT", model="corporate-a",
    ) == 1


def test_an_unauthorized_model_counts_as_an_auth_failure():
    before = value("intentlatch_auth_failures_total", reason="model_not_authorized")
    prompt = decision("blocked", [result("AUTH-MODEL", "authority", "block", "violated")], ["AUTH-MODEL"])
    metrics.observe(trace("T-AUTH", prompt, upstream_ms=0.0, tokens_in=0, tokens_out=0), total_ms=1.0)
    assert value("intentlatch_auth_failures_total", reason="model_not_authorized") == before + 1


def test_agent_failures_and_upstream_errors_are_counted_by_reason():
    errors = value("intentlatch_control_agent_errors_total", reason="unparseable")
    verdicts = value("intentlatch_control_agent_verdicts_total", direction="prompt", status="error")
    upstream = value("intentlatch_upstream_errors_total", model="corporate-a", reason="upstream_timeout")
    prompt = decision("allowed", [result("AI-X", None, "block", "error")], status="error", failure="unparseable")
    metrics.observe(trace("T-FAIL", prompt, upstream_error="upstream_timeout", upstream_ms=0.0), total_ms=1.0)
    assert value("intentlatch_control_agent_errors_total", reason="unparseable") == errors + 1
    assert value("intentlatch_control_agent_verdicts_total", direction="prompt", status="error") == verdicts + 1
    assert value("intentlatch_upstream_errors_total", model="corporate-a", reason="upstream_timeout") == upstream + 1


def test_a_skipped_agent_records_no_verdict():
    before = value("intentlatch_control_agent_verdicts_total", direction="response", status="skipped")
    metrics.observe(trace("T-SKIP", decision("allowed", []), decision("allowed", [])), total_ms=1.0)
    assert value("intentlatch_control_agent_verdicts_total", direction="response", status="skipped") == before == 0


def test_active_policies_are_counted_per_kind_including_zero():
    policies = [
        Policy(code="AUTH-MODEL", ai=False, text=None, kind="authority", params={}, action="block", applies_to="prompt"),
        Policy(code="AI-A", ai=True, text="t", kind=None, params={}, action="block", applies_to="both"),
        Policy(code="AI-B", ai=True, text="t", kind=None, params={}, action="edit", applies_to="both"),
    ]
    metrics.set_active_policies(PolicySnapshot(version=1, policies=policies))
    counts = {kind: value("intentlatch_policies_active", kind=kind) for kind in metrics.KINDS}
    assert counts == {"authority": 1, "limit": 0, "regex": 0, "ai": 2}


def test_usage_ratio_is_the_strictest_applying_limit():
    def limit(code, max_tokens, window):
        params = {"max_tokens": max_tokens, "window_seconds": window, "team": None}
        return Policy(code=code, ai=False, text=None, kind="limit", params=params, action="block", applies_to="prompt")

    ends = datetime(2026, 10, 4, tzinfo=UTC)
    usage = {60: WindowUsage(used=50, ends=ends), 3600: WindowUsage(used=900, ends=ends)}
    metrics.set_usage_ratio("T-RATIO", [limit("LIM-M", 100, 60), limit("LIM-H", 1000, 3600)], usage)
    assert value("intentlatch_team_token_usage_ratio", team="T-RATIO") == 0.9
    metrics.set_usage_ratio("T-NONE", [], {})
    assert metrics.REGISTRY.get_sample_value("intentlatch_team_token_usage_ratio", {"team": "T-NONE"}) is None


def test_token_errors_map_to_bounded_auth_failure_reasons():
    assert set(AUTH_FAILURE_REASONS) == set(TOKEN_MESSAGES)
    assert sorted(AUTH_FAILURE_REASONS.values()) == ["invalid", "missing", "revoked"]


def test_the_exposition_lists_every_metric_and_no_free_text():
    text = metrics.render().decode()
    for name in METRIC_NAMES:
        assert f"# TYPE {name.removesuffix('_total')}" in text or f"# TYPE {name}" in text
    assert "Ana" not in text
    assert metrics.CONTENT_TYPE.startswith("text/plain; version=0.0.4")
