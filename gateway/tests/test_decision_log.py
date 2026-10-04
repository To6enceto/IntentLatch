import base64
import re
import uuid
from datetime import UTC, datetime

import pytest

from intentlatch.decision_log import (
    GENESIS,
    canonical,
    chain,
    counted_spans,
    mask_text,
    record_hash,
    records,
    replace_spans,
)
from intentlatch.identity import Identity
from intentlatch.pipeline import Decision, PolicyResult
from intentlatch.policies import SNAPSHOT_FIELDS, Policy, PolicySnapshot, load_seeds
from intentlatch.trace import Stage, Trace

EMPLOYEE = uuid.uuid4()
IDENTITY = Identity(
    employee_id=EMPLOYEE, employee_name="Ana", team_id=uuid.uuid4(), team_name="Payments", authorized_models=["corporate-a"]
)
NOW = datetime(2026, 10, 4, 1, 2, 3, 456789, tzinfo=UTC)


def regex(code: str, pattern: str, action: str = "edit") -> Policy:
    return Policy(code=code, ai=False, text=None, kind="regex", params={"pattern": pattern}, action=action, applies_to="both")


MAIL = regex("RGX-MAIL", r"\b\w+@\w+\.pl\b")
CARD = regex("RGX-CARD", r"\b(?P<luhn>\d{4}(?: ?\d{4}){3})\b")
SNAPSHOT = PolicySnapshot(version=9, policies=[MAIL, CARD])


def result(code: str, result: str = "pass", reasoning: str | None = None, ai: bool = False) -> PolicyResult:
    return PolicyResult(
        code=code, kind=None if ai else "regex", ai=ai, action="edit", result=result, reasoning=reasoning, latency_ms=1.5
    )


def test_raw_matches_become_their_code():
    assert mask_text("mail jan@firma.pl or ola@firma.pl", [MAIL]) == "mail [RGX-MAIL] or [RGX-MAIL]"


def test_luhn_patterns_mask_only_checksum_valid_captures():
    text = "pay 4111 1111 1111 1111, not 4111 1111 1111 1112"
    assert mask_text(text, [CARD]) == "pay [RGX-CARD], not 4111 1111 1111 1112"


def test_a_value_only_a_normalized_view_shows_masks_the_whole_piece():
    encoded = base64.b64encode(b"jan@firma.pl").decode()
    assert mask_text(f"see {encoded}", [MAIL, CARD]) == "[masked: RGX-MAIL]"
    assert mask_text("jan@​firma.pl", [MAIL]) == "[masked: RGX-MAIL]"


SEEDED_CARD = Policy(**next(seed for seed in load_seeds() if seed.code == "RGX-CARD").model_dump(include=set(SNAPSHOT_FIELDS)))


@pytest.mark.parametrize(
    ("text", "masked"),
    [
        ("pay 4111 1111 1111 1111 12/26 now", "pay [RGX-CARD] 12/26 now"),
        ("from 2345 4111 1111 1111 1111", "from 2345 [RGX-CARD]"),
        ("cards 4111111111111111 and 5555 5555 5555 4444.", "cards [RGX-CARD] and [RGX-CARD]."),
        ("not 4111 1111 1111 1112 12/26", "not 4111 1111 1111 1112 12/26"),
    ],
)
def test_a_lookahead_luhn_pattern_masks_only_the_card_digits(text, masked):
    assert mask_text(text, [SEEDED_CARD]) == masked


def test_overlapping_captures_mask_once():
    # 18, 83 and 34 all pass the checksum and overlap, so one placeholder covers 1834.
    pairs = regex("RGX-PAIR", r"(?=(?P<luhn>\d\d))")
    assert mask_text("x1834y", [pairs]) == "x[RGX-PAIR]y"
    assert counted_spans(re.compile(r"(?=(?P<luhn>\d\d))"), "x1834y 59") == [(1, 5), (7, 9)]
    assert replace_spans("abcdef", [(1, 3), (4, 5)], "[X]") == "a[X]d[X]f"


def test_text_without_matches_is_unchanged():
    assert mask_text("Hello there", [MAIL, CARD]) == "Hello there"
    assert mask_text("jan@firma.pl", []) == "jan@firma.pl"


def trace(**changes) -> Trace:
    edited = Decision(
        outcome="edited",
        policy_version=9,
        policy_results=[result("RGX-MAIL", "violated", "Mentions jan@firma.pl."), result("AI-X", ai=True)],
        rewritten="mail [removed]",
        control_agent_status="modified",
        responsible=["RGX-MAIL"],
        rewrites=["mail [removed]"],
    )
    answered = Decision(outcome="allowed", policy_version=9, policy_results=[], control_agent_status="skipped")
    fields = {
        "request_id": "2f6a3c1e-0000-4000-8000-000000000001",
        "identity": IDENTITY,
        "model": "corporate-a",
        "auth_ms": 2.5,
        "snapshot": SNAPSHOT,
        "prompt": Stage(edited, ["mail jan@firma.pl"], non_ai_ms=3.0, agent_ms=4000.0, agent_seconds=3.25),
        "response": Stage(answered, ["Sure, done. Reply to ola@firma.pl"], non_ai_ms=1.0),
        "upstream_ms": 5000.0,
        "upstream_seconds": 4.5,
        "tokens_in": 31,
        "tokens_out": 12,
    }
    return Trace(**(fields | changes))


def test_prompt_and_response_records_split_request_level_fields_onto_the_prompt():
    prompt, response = records(trace(), total_ms=9100.0, now=NOW)
    assert prompt | {"id": "x"} == {
        "id": "x",
        "request_id": "2f6a3c1e-0000-4000-8000-000000000001",
        "ts": "2026-10-04T01:02:03.456789+00:00",
        "employee_id": str(EMPLOYEE),
        "team": "Payments",
        "model": "corporate-a",
        "direction": "prompt",
        "outcome": "edited",
        "control_agent_status": "modified",
        "policy_results": [
            {"code": "RGX-MAIL", "kind": "regex", "ai": False, "action": "edit", "result": "violated", "reasoning": "Mentions [RGX-MAIL].", "latency_ms": 1.5},
            {"code": "AI-X", "kind": None, "ai": True, "action": "edit", "result": "pass", "reasoning": None, "latency_ms": 1.5},
        ],
        "source_masked": "mail [RGX-MAIL]",
        "rewritten_masked": "mail [removed]",
        "tokens_in": 31,
        "tokens_out": 12,
        "compute_seconds": "7.750",
        "stage_latency_ms": {"auth": 2.5, "non_ai": 3.0, "control_agent": 4000.0, "upstream": 5000.0, "total": 9100.0},
        "policy_version": 9,
        "feed_version": None,
        "test_run_id": None,
    }
    assert (response["direction"], response["outcome"], response["source_masked"]) == (
        "response",
        "allowed",
        "Sure, done. Reply to [RGX-MAIL]",
    )
    assert (response["tokens_in"], response["tokens_out"], response["compute_seconds"]) == (0, 0, "0.000")
    assert response["stage_latency_ms"] == {"auth": 0.0, "non_ai": 1.0, "control_agent": 0.0, "upstream": 0.0, "total": 9100.0}
    assert response["rewritten_masked"] is None
    assert uuid.UUID(prompt["id"]) != uuid.UUID(response["id"])


def test_a_blocked_prompt_writes_one_record():
    assert [entry["direction"] for entry in records(trace(response=None), total_ms=10.0, now=NOW)] == ["prompt"]


def test_no_raw_sensitive_value_reaches_a_record():
    for entry in records(trace(), total_ms=1.0, now=NOW):
        assert "jan@firma.pl" not in canonical(entry) and "ola@firma.pl" not in canonical(entry)


def test_canonical_form_sorts_keys_without_spaces():
    assert canonical({"b": 1, "a": {"d": None, "c": "ż"}}) == '{"a":{"c":"ż","d":null},"b":1}'


def test_hash_covers_the_previous_hash_and_every_field():
    entry = records(trace(), total_ms=1.0, now=NOW)[0]
    digest = record_hash(GENESIS, entry)
    assert len(digest) == 64 and int(digest, 16) >= 0
    assert record_hash(GENESIS, dict(reversed(list(entry.items())))) == digest
    assert record_hash("1" * 64, entry) != digest
    assert record_hash(GENESIS, entry | {"outcome": "allowed"}) != digest


def test_chain_links_each_record_to_the_one_before():
    first, second = records(trace(), total_ms=1.0, now=NOW)
    links = chain([first, second], GENESIS)
    assert links[0] == (GENESIS, record_hash(GENESIS, first))
    assert links[1] == (links[0][1], record_hash(links[0][1], second))
    assert chain([], GENESIS) == []
