import json
import uuid

from intentlatch.errors import error_response
from intentlatch.identity import Identity
from intentlatch.pipeline import authority_result, blocked_error, evaluate_prompt
from intentlatch.policies import Policy, PolicySnapshot

IDENTITY = Identity(
    employee_id=uuid.uuid4(),
    employee_name="Ana",
    team_id=uuid.uuid4(),
    team_name="Payments",
    authorized_models=["corporate-a"],
)


def policy(code: str, kind: str | None = "authority", **changes) -> Policy:
    fields = {
        "code": code,
        "ai": kind is None,
        "text": "No credentials." if kind is None else None,
        "kind": kind,
        "params": {},
        "action": "block",
        "applies_to": "prompt",
    }
    return Policy(**(fields | changes))


def snapshot(*policies: Policy, version: int = 7) -> PolicySnapshot:
    return PolicySnapshot(version=version, policies=list(policies))


def body(response) -> dict:
    return json.loads(response.body)


def test_authority_passes_for_an_authorized_model():
    result = authority_result(policy("AUTH-MODEL"), IDENTITY, "corporate-a")
    assert (result.code, result.kind, result.ai, result.action) == ("AUTH-MODEL", "authority", False, "block")
    assert (result.result, result.reasoning) == ("pass", None)


def test_authority_violation_names_team_and_model():
    result = authority_result(policy("AUTH-MODEL"), IDENTITY, "corporate-b")
    assert result.result == "violated"
    assert result.reasoning == "Team Payments is not authorized for corporate-b."


def test_no_policies_is_allowed_with_the_version():
    decision = evaluate_prompt(snapshot(version=3), IDENTITY, "corporate-b")
    assert (decision.outcome, decision.policy_version, decision.policy_results) == ("allowed", 3, [])


def test_passing_authority_policy_allows():
    decision = evaluate_prompt(snapshot(policy("AUTH-MODEL")), IDENTITY, "corporate-a")
    assert decision.outcome == "allowed"
    assert [entry.result for entry in decision.policy_results] == ["pass"]


def test_authority_violation_blocks():
    decision = evaluate_prompt(snapshot(policy("AUTH-MODEL")), IDENTITY, "corporate-b")
    assert decision.outcome == "blocked"
    assert [entry.result for entry in decision.policy_results] == ["violated"]


def test_each_authority_policy_gives_one_result_in_order():
    decision = evaluate_prompt(snapshot(policy("AUTH-A"), policy("AUTH-B")), IDENTITY, "corporate-a")
    assert [entry.code for entry in decision.policy_results] == ["AUTH-A", "AUTH-B"]


def test_other_kinds_and_ai_policies_add_no_results_yet():
    others = (
        policy("AI-X", kind=None, applies_to="both"),
        policy("LIM-X", kind="limit", params={"max_tokens": 1, "window_seconds": 60, "team": None}),
        policy("RGX-X", kind="regex", params={"pattern": "x"}, action="edit", applies_to="both"),
    )
    allowed = evaluate_prompt(snapshot(*others), IDENTITY, "corporate-b")
    assert (allowed.outcome, allowed.policy_results) == ("allowed", [])
    blocked = evaluate_prompt(snapshot(*others, policy("AUTH-MODEL")), IDENTITY, "corporate-b")
    assert blocked.outcome == "blocked"
    assert [entry.code for entry in blocked.policy_results] == ["AUTH-MODEL"]


def test_rewritten_is_null_and_latency_is_non_negative():
    decision = evaluate_prompt(snapshot(policy("AUTH-MODEL")), IDENTITY, "corporate-a")
    assert decision.rewritten is None
    latency = decision.policy_results[0].latency_ms
    assert isinstance(latency, float) and latency >= 0


def test_blocked_error_names_every_violated_block_policy():
    decision = evaluate_prompt(snapshot(policy("AUTH-A"), policy("AUTH-B"), version=4), IDENTITY, "corporate-b")
    error = blocked_error(decision)
    reason = "Team Payments is not authorized for corporate-b."
    assert (error.status_code, error.code) == (403, "policy_blocked")
    assert error.message == f"Blocked by AUTH-A: {reason}; AUTH-B: {reason}"
    assert error.extra == {
        "policies": [
            {"code": "AUTH-A", "kind": "authority", "reasoning": reason},
            {"code": "AUTH-B", "kind": "authority", "reasoning": reason},
        ],
        "policy_version": 4,
    }


def test_error_response_adds_extra_fields_after_code_and_message():
    response = error_response(403, "policy_blocked", "Blocked", extra={"policy_version": 2})
    assert response.status_code == 403
    error = body(response)["error"]
    assert list(error) == ["code", "message", "policy_version"]
    assert error == {"code": "policy_blocked", "message": "Blocked", "policy_version": 2}


def test_error_response_without_extra_keeps_the_old_shape():
    assert body(error_response(404, "model_not_found", "Unknown model.")) == {
        "error": {"code": "model_not_found", "message": "Unknown model."}
    }
