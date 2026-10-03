import base64
import json
import uuid
from datetime import UTC, datetime

import pytest

from intentlatch.errors import error_response
from intentlatch.identity import Identity
from intentlatch.limits import WindowUsage
from intentlatch.pipeline import (
    authority_result,
    blocked_error,
    evaluate_prompt,
    evaluate_response,
    luhn_valid,
    pattern_matches,
)
from intentlatch.policies import Policy, PolicySnapshot

IDENTITY = Identity(
    employee_id=uuid.uuid4(),
    employee_name="Ana",
    team_id=uuid.uuid4(),
    team_name="Payments",
    authorized_models=["corporate-a"],
)
PROMPT_REASON = "The prompt matches this policy's pattern."
RESPONSE_REASON = "The response matches this policy's pattern."
CARD_PATTERN = r"\b(?P<luhn>\d{4}(?: ?\d{4}){3})\b"
WINDOW_END = datetime(2026, 10, 4, 2, 0, tzinfo=UTC)


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


def regex(code: str, pattern: str, action: str = "block", applies_to: str = "both") -> Policy:
    return policy(code, kind="regex", params={"pattern": pattern}, action=action, applies_to=applies_to)


def limit(code: str, max_tokens: int = 1000, window_seconds: int = 3600, team: str | None = "Payments") -> Policy:
    params = {"max_tokens": max_tokens, "window_seconds": window_seconds, "team": team}
    return policy(code, kind="limit", params=params)


def used(tokens: int, window_seconds: int = 3600) -> dict[int, WindowUsage]:
    return {window_seconds: WindowUsage(used=tokens, ends=WINDOW_END)}


def limit_reason(tokens: int) -> str:
    return (
        f"Team Payments has used {tokens} of 1000 tokens in the 3600-second window"
        " ending 2026-10-04T02:00:00+00:00."
    )


def snapshot(*policies: Policy, version: int = 7) -> PolicySnapshot:
    return PolicySnapshot(version=version, policies=list(policies))


def prompt(*policies: Policy, texts: list[str], model: str = "corporate-a", usage: dict | None = None):
    return evaluate_prompt(snapshot(*policies), IDENTITY, model, texts, usage or {})


def response(*policies: Policy, texts: list[str]):
    return evaluate_response(snapshot(*policies), texts)


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
    decision = evaluate_prompt(snapshot(version=3), IDENTITY, "corporate-b", ["Hello"], {})
    assert (decision.outcome, decision.policy_version, decision.policy_results) == ("allowed", 3, [])


def test_passing_authority_policy_allows():
    decision = prompt(policy("AUTH-MODEL"), texts=["Hello"])
    assert decision.outcome == "allowed"
    assert [entry.result for entry in decision.policy_results] == ["pass"]


def test_authority_violation_blocks():
    decision = prompt(policy("AUTH-MODEL"), texts=["Hello"], model="corporate-b")
    assert decision.outcome == "blocked"
    assert [entry.result for entry in decision.policy_results] == ["violated"]


def test_each_authority_policy_gives_one_result_in_order():
    decision = prompt(policy("AUTH-A"), policy("AUTH-B"), texts=["Hello"])
    assert [entry.code for entry in decision.policy_results] == ["AUTH-A", "AUTH-B"]


def test_ai_policies_add_no_results_yet():
    ai = policy("AI-X", kind=None, applies_to="both")
    allowed = prompt(ai, texts=["x"], model="corporate-b")
    assert (allowed.outcome, allowed.policy_results) == ("allowed", [])
    blocked = prompt(ai, policy("AUTH-MODEL"), texts=["x"], model="corporate-b")
    assert blocked.outcome == "blocked"
    assert [entry.code for entry in blocked.policy_results] == ["AUTH-MODEL"]


def test_limit_passes_below_max_tokens():
    decision = prompt(limit("LIM-X"), texts=["Hello"], usage=used(999))
    assert decision.outcome == "allowed"
    [result] = decision.policy_results
    assert (result.code, result.kind, result.ai, result.action) == ("LIM-X", "limit", False, "block")
    assert (result.result, result.reasoning) == ("pass", None)


@pytest.mark.parametrize("tokens", [1000, 1204])
def test_limit_is_violated_at_or_over_max_tokens(tokens):
    decision = prompt(limit("LIM-X"), texts=["Hello"], usage=used(tokens))
    assert decision.outcome == "blocked"
    [result] = decision.policy_results
    assert (result.result, result.reasoning) == ("violated", limit_reason(tokens))


def test_a_team_limit_applies_to_its_team_and_a_null_team_to_every_team():
    policies = (limit("LIM-ALL", team=None), limit("LIM-OTHER", team="Research"), limit("LIM-OWN"))
    decision = prompt(*policies, texts=["Hello"], usage=used(5))
    assert [entry.code for entry in decision.policy_results] == ["LIM-ALL", "LIM-OWN"]


def test_each_limit_reads_its_own_window():
    usage = {60: WindowUsage(used=50, ends=WINDOW_END), 3600: WindowUsage(used=900, ends=WINDOW_END)}
    policies = (limit("LIM-HOUR", max_tokens=800), limit("LIM-MINUTE", max_tokens=100, window_seconds=60))
    decision = prompt(*policies, texts=["Hello"], usage=usage)
    assert [(entry.code, entry.result) for entry in decision.policy_results] == [
        ("LIM-HOUR", "violated"),
        ("LIM-MINUTE", "pass"),
    ]


def test_a_limit_block_yields_no_regex_results():
    decision = prompt(limit("LIM-X"), regex("RGX-X", "x"), texts=["x"], usage=used(1000))
    assert decision.outcome == "blocked"
    assert [entry.code for entry in decision.policy_results] == ["LIM-X"]


@pytest.mark.parametrize("usage", [{}, used(0, window_seconds=60)], ids=["none", "other-window"])
def test_a_limit_without_loaded_usage_raises_instead_of_passing(usage):
    with pytest.raises(KeyError):
        prompt(limit("LIM-X"), texts=["Hello"], usage=usage)


def test_a_limit_only_block_is_429_limit_exceeded():
    error = blocked_error(prompt(limit("LIM-X"), texts=["Hello"], usage=used(1204)))
    assert (error.status_code, error.code, error.headers) == (429, "limit_exceeded", None)
    assert error.message == f"Blocked by LIM-X: {limit_reason(1204)}"
    assert error.extra == {
        "policies": [{"code": "LIM-X", "kind": "limit", "reasoning": limit_reason(1204)}],
        "policy_version": 7,
    }


def test_an_authority_and_limit_block_stays_403_naming_both():
    policies = (policy("AUTH-MODEL"), limit("LIM-X"))
    error = blocked_error(prompt(*policies, texts=["Hello"], model="corporate-b", usage=used(1204)))
    assert (error.status_code, error.code) == (403, "policy_blocked")
    assert [entry["code"] for entry in error.extra["policies"]] == ["AUTH-MODEL", "LIM-X"]
    assert error.extra["policy_version"] == 7


def test_limit_reasoning_and_error_hold_no_prompt_text():
    decision = prompt(limit("LIM-X"), texts=["my AKIASECRET"], usage=used(1204))
    error = blocked_error(decision)
    assert "AKIASECRET" not in decision.model_dump_json()
    assert "AKIASECRET" not in error.message + json.dumps(error.extra)


def test_rewritten_is_null_and_latency_is_non_negative():
    decision = prompt(policy("AUTH-MODEL"), regex("RGX-X", "x"), texts=["x"])
    assert decision.rewritten is None
    for entry in decision.policy_results:
        assert isinstance(entry.latency_ms, float) and entry.latency_ms >= 0


def test_regex_block_match_blocks():
    decision = prompt(regex("RGX-KEY", r"AKIA\d+"), texts=["key AKIA123"])
    assert decision.outcome == "blocked"
    [result] = decision.policy_results
    assert (result.code, result.kind, result.ai, result.action) == ("RGX-KEY", "regex", False, "block")
    assert (result.result, result.reasoning) == ("violated", PROMPT_REASON)


def test_edit_only_match_blocks_naming_the_edit_policy():
    decision = prompt(regex("RGX-MAIL", "@", action="edit"), texts=["a@b"])
    assert (decision.outcome, decision.rewritten) == ("blocked", None)
    assert blocked_error(decision).extra["policies"] == [
        {"code": "RGX-MAIL", "kind": "regex", "reasoning": PROMPT_REASON}
    ]


def test_block_and_edit_matches_name_only_the_block_policy():
    decision = prompt(regex("RGX-A", "a", action="edit"), regex("RGX-B", "b"), texts=["ab"])
    assert [entry.result for entry in decision.policy_results] == ["violated", "violated"]
    assert [entry["code"] for entry in blocked_error(decision).extra["policies"]] == ["RGX-B"]


def test_no_match_is_allowed_with_pass_results():
    decision = prompt(regex("RGX-A", "a"), regex("RGX-B", "b", action="edit"), texts=["xyz"])
    assert decision.outcome == "allowed"
    assert [(entry.result, entry.reasoning) for entry in decision.policy_results] == [
        ("pass", None),
        ("pass", None),
    ]


@pytest.mark.parametrize(
    "texts",
    [
        ["clean", "the secret"],
        ["the se%63ret"],
        ["the se​cret"],
        [base64.b64encode(b"my secret value").decode()],
    ],
    ids=["second-piece", "percent", "zero-width", "base64"],
)
def test_a_match_in_any_piece_or_view_violates(texts):
    assert prompt(regex("RGX-SECRET", "secret"), texts=texts).outcome == "blocked"


def test_no_texts_pass_every_regex_policy():
    decision = prompt(regex("RGX-X", "x"), texts=[])
    assert (decision.outcome, [entry.result for entry in decision.policy_results]) == ("allowed", ["pass"])


def test_response_only_policy_is_ignored_on_prompts():
    policies = (regex("RGX-ANSWER", "x", applies_to="response"), regex("RGX-ASK", "y", applies_to="prompt"))
    decision = prompt(*policies, texts=["xy"])
    assert [entry.code for entry in decision.policy_results] == ["RGX-ASK"]


def test_an_authority_block_yields_no_regex_results():
    decision = prompt(policy("AUTH-MODEL"), regex("RGX-X", "x"), texts=["x"], model="corporate-b")
    assert decision.outcome == "blocked"
    assert [entry.code for entry in decision.policy_results] == ["AUTH-MODEL"]


def test_results_come_authority_first_then_regex_in_code_order():
    decision = prompt(regex("A-RGX", "q"), policy("AUTH-MODEL"), regex("B-RGX", "q"), texts=["text"])
    assert [entry.code for entry in decision.policy_results] == ["AUTH-MODEL", "A-RGX", "B-RGX"]


def test_limit_results_come_after_authority_and_before_regex():
    policies = (regex("A-RGX", "q"), policy("AUTH-MODEL"), limit("LIM-X"), regex("Z-RGX", "q"))
    decision = prompt(*policies, texts=["text"], usage=used(0))
    assert [entry.code for entry in decision.policy_results] == ["AUTH-MODEL", "LIM-X", "A-RGX", "Z-RGX"]


def test_violation_reasoning_and_error_hold_no_prompt_text():
    decision = prompt(regex("RGX-KEY", r"AKIA\w+"), texts=["my AKIASECRET"])
    error = blocked_error(decision)
    assert "AKIASECRET" not in decision.model_dump_json()
    assert "AKIASECRET" not in error.message + json.dumps(error.extra)


def test_luhn_group_counts_only_valid_captures():
    card = regex("RGX-CARD", CARD_PATTERN)
    assert prompt(card, texts=["pay 4111 1111 1111 1111"]).outcome == "blocked"
    assert prompt(card, texts=["pay 4111 1111 1111 1112"]).outcome == "allowed"


def test_a_luhn_pattern_keeps_looking_past_invalid_matches():
    assert pattern_matches(CARD_PATTERN, ["4111111111111112 or 4111111111111111"])


@pytest.mark.parametrize("pattern", [r"(?P<luhn>[a-z]+)", r"(?P<luhn>\d+)?x"])
def test_a_luhn_match_without_captured_digits_does_not_count(pattern):
    assert not pattern_matches(pattern, ["abc x"])


def test_pattern_matches_any_view():
    assert pattern_matches("b", ["a", "abc"])
    assert not pattern_matches("z", ["a", "abc"])
    assert not pattern_matches("z", [])


@pytest.mark.parametrize(
    ("digits", "valid"),
    [
        ("4111111111111111", True),
        ("4111111111111112", False),
        ("378282246310005", True),
        ("18", True),
        ("0", False),
        ("", False),
    ],
)
def test_luhn_valid(digits, valid):
    assert luhn_valid([int(char) for char in digits]) is valid


def test_response_checks_only_response_and_both_regex_policies():
    policies = (
        policy("AUTH-MODEL"),
        limit("LIM-X", max_tokens=1),
        regex("RGX-ANSWER", "x", applies_to="response"),
        regex("RGX-ASK", "x", applies_to="prompt"),
        regex("RGX-EITHER", "x", applies_to="both"),
    )
    decision = response(*policies, texts=["x"])
    assert [entry.code for entry in decision.policy_results] == ["RGX-ANSWER", "RGX-EITHER"]
    assert (decision.outcome, decision.policy_version, decision.rewritten) == ("blocked", 7, None)


def test_response_block_names_the_response_in_the_error():
    decision = response(regex("RGX-LINK", r"!\[", applies_to="response"), texts=["see ![x](u)"])
    error = blocked_error(decision)
    assert error.message == f"Blocked by RGX-LINK: {RESPONSE_REASON}"
    assert error.extra == {
        "policies": [{"code": "RGX-LINK", "kind": "regex", "reasoning": RESPONSE_REASON}],
        "policy_version": 7,
    }


def test_response_edit_only_match_blocks_naming_the_edit_policy():
    decision = response(regex("RGX-MAIL", "@", action="edit"), regex("RGX-KEY", "AKIA"), texts=["a@b"])
    assert decision.outcome == "blocked"
    assert [entry["code"] for entry in blocked_error(decision).extra["policies"]] == ["RGX-MAIL"]


def test_response_block_and_edit_matches_name_only_the_block_policy():
    policies = (regex("RGX-MAIL", "@", action="edit"), regex("RGX-KEY", "AKIA"))
    decision = response(*policies, texts=["a@b", "AKIA"])
    assert [entry["code"] for entry in blocked_error(decision).extra["policies"]] == ["RGX-KEY"]


def test_clean_response_is_allowed():
    decision = response(regex("RGX-KEY", "AKIA"), texts=["fine", "also fine"])
    assert (decision.outcome, [entry.result for entry in decision.policy_results]) == ("allowed", ["pass"])


def test_blocked_error_names_every_violated_block_policy():
    decision = evaluate_prompt(
        snapshot(policy("AUTH-A"), policy("AUTH-B"), version=4), IDENTITY, "corporate-b", ["Hello"], {}
    )
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
