import uuid
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from intentlatch import policies
from intentlatch.chat import Piece
from intentlatch.control_agent import Verdict
from intentlatch.errors import GatewayError, describe_validation_error
from intentlatch.identity import Identity
from intentlatch.pipeline import evaluate_prompt, first_stages
from intentlatch.policies import SNAPSHOT_FIELDS, Policy, PolicySnapshot
from intentlatch.testcases import (
    CASE_FIELDS,
    INSERT_CASE,
    TEST_EMPLOYEE_ID,
    TEST_EMPLOYEE_NAME,
    TEST_TEAM_MODELS,
    TEST_TEAM_NAME,
    UPDATE_CASE,
    Case,
    CaseCreate,
    CaseUpdate,
    case_json,
    field_values,
    load_seeds,
    merge_case_update,
    missing_codes,
    update_case,
)

EDIT_CASE = {
    "code": "tc-mail",
    "name": "  Mail is removed  ",
    "prompt": " Write to jan@firma.pl ",
    "model": "corporate-a",
    "expected": "EDIT",
    "expected_policy_code": "rgx-email",
    "must_not_contain": ["  jan@firma.pl "],
}
ALLOW_CASE = {"code": "TC-HELLO", "name": "Hello", "prompt": "Hello", "model": "corporate-b", "expected": "ALLOW"}

POLICY_SEEDS = {seed.code: seed for seed in policies.load_seeds()}
SNAPSHOT = PolicySnapshot(
    version=1,
    policies=[
        Policy(**seed.model_dump(include=set(SNAPSHOT_FIELDS))) for seed in POLICY_SEEDS.values() if seed.enabled
    ],
)
TEST_IDENTITY = Identity(
    employee_id=TEST_EMPLOYEE_ID,
    employee_name=TEST_EMPLOYEE_NAME,
    team_id=uuid.uuid4(),
    team_name=TEST_TEAM_NAME,
    authorized_models=TEST_TEAM_MODELS,
)
SEEDS = load_seeds()


def make(base: dict, **changes) -> CaseCreate:
    return CaseCreate.model_validate({**base, **changes})


def rejected(base: dict, match: str | None = None, **changes) -> None:
    with pytest.raises(ValidationError, match=match):
        make(base, **changes)


def test_a_case_is_normalized_and_its_prompt_kept_verbatim():
    case = make(EDIT_CASE)
    assert (case.code, case.name, case.expected_policy_code) == ("TC-MAIL", "Mail is removed", "RGX-EMAIL")
    assert case.prompt == " Write to jan@firma.pl "
    assert case.must_not_contain == ["jan@firma.pl"]


def test_a_case_runs_as_the_test_employee_unless_told_otherwise():
    assert make(ALLOW_CASE).run_as_employee_id == TEST_EMPLOYEE_ID
    other = uuid.uuid4()
    assert make(ALLOW_CASE, run_as_employee_id=str(other)).run_as_employee_id == other
    rejected(ALLOW_CASE, run_as_employee_id="ana")
    assert make(ALLOW_CASE).must_not_contain == []


@pytest.mark.parametrize("code", ["", "A", "x" * 65, "1ABC", "A--B", "A B", 42])
def test_invalid_codes_are_rejected(code):
    rejected(ALLOW_CASE, code=code)
    rejected(EDIT_CASE, expected_policy_code=code)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("name", "   "),
        ("name", "x" * 129),
        ("prompt", ""),
        ("prompt", "  \n "),
        ("prompt", "x" * 4001),
        ("prompt", 42),
        ("model", "gpt-4"),
        ("model", "Corporate-A"),
        ("expected", "allow"),
        ("expected", None),
    ],
)
def test_out_of_range_fields_are_rejected(field, value):
    rejected(ALLOW_CASE, **{field: value})


def test_limits_are_inclusive():
    case = make(EDIT_CASE, name="n" * 128, prompt="p" * 4000, must_not_contain=["m" * 200] * 20)
    assert (len(case.name), len(case.prompt), len(case.must_not_contain)) == (128, 4000, 20)


@pytest.mark.parametrize("entries", [[""], ["  "], ["m" * 201], ["x"] * 21, [7]])
def test_bad_must_not_contain_entries_are_rejected(entries):
    rejected(EDIT_CASE, must_not_contain=entries)


def test_expected_policy_code_is_not_allowed_on_allow():
    rejected(ALLOW_CASE, expected_policy_code="RGX-EMAIL", match="only allowed when expected is EDIT or BLOCK")
    assert make(ALLOW_CASE, expected="BLOCK", expected_policy_code="auth-model").expected_policy_code == "AUTH-MODEL"


@pytest.mark.parametrize("expected", ["ALLOW", "BLOCK"])
def test_must_not_contain_is_only_for_edit(expected):
    rejected(EDIT_CASE, expected=expected, expected_policy_code=None, match="only allowed when expected is EDIT")


@pytest.mark.parametrize("field", ["name", "prompt"])
@pytest.mark.parametrize("bad", ["a\x00b", "a\ud800b"])
def test_nul_and_unpaired_surrogates_are_rejected(field, bad):
    rejected(EDIT_CASE, **{field: bad})
    rejected(EDIT_CASE, must_not_contain=[bad])


def test_unknown_and_server_owned_fields_are_rejected():
    rejected(ALLOW_CASE, predefined=True)
    rejected(ALLOW_CASE, token="x")


def test_shape_errors_read_without_a_prefix():
    with pytest.raises(ValidationError) as caught:
        make(ALLOW_CASE, expected_policy_code="RGX-EMAIL")
    assert describe_validation_error(caught.value) == (
        "expected_policy_code is only allowed when expected is EDIT or BLOCK"
    )


def stored(base: dict) -> dict:
    return make(base).model_dump()


def test_update_keeps_unspecified_fields():
    merged = merge_case_update(stored(EDIT_CASE), CaseUpdate(name="Renamed"))
    assert merged.model_dump() == {**stored(EDIT_CASE), "name": "Renamed"}


def test_update_can_clear_the_expected_policy_code():
    assert merge_case_update(stored(EDIT_CASE), CaseUpdate(expected_policy_code=None)).expected_policy_code is None


def test_update_is_revalidated_after_merging():
    current = stored(EDIT_CASE)
    with pytest.raises(ValidationError, match="must_not_contain is only allowed when expected is EDIT"):
        merge_case_update(current, CaseUpdate(expected="BLOCK"))
    merged = merge_case_update(current, CaseUpdate(expected="BLOCK", must_not_contain=[]))
    assert (merged.expected, merged.must_not_contain) == ("BLOCK", [])
    with pytest.raises(ValidationError):
        merge_case_update(current, CaseUpdate.model_validate({"name": None}))
    with pytest.raises(ValidationError):
        merge_case_update(current, CaseUpdate(model="gpt-4"))


@pytest.mark.parametrize("body", [{}, {"code": "TC-X"}, {"predefined": False}, {"name": "x", "extra": 1}])
def test_empty_or_unknown_updates_are_rejected(body):
    with pytest.raises(ValidationError):
        CaseUpdate.model_validate(body)


def test_statements_bind_values_in_column_order():
    case = make(EDIT_CASE)
    assert INSERT_CASE.startswith(f"INSERT INTO test_cases ({', '.join(CASE_FIELDS)}, predefined)")
    assert INSERT_CASE.count("%s") == len(field_values(case)) + 1
    assert field_values(case) == (
        "TC-MAIL", "Mail is removed", " Write to jan@firma.pl ", "corporate-a", TEST_EMPLOYEE_ID,
        "EDIT", "RGX-EMAIL", ["jan@firma.pl"],
    )
    assert UPDATE_CASE.startswith("UPDATE test_cases SET name = %s, prompt = %s, model = %s, run_as_employee_id = %s,")
    assert UPDATE_CASE.count("%s") == len(CASE_FIELDS)


def test_case_rows_become_json():
    stamp = datetime(2026, 10, 4, 6, 0, tzinfo=UTC)
    row = (*field_values(make(EDIT_CASE)), True, stamp, stamp)
    assert case_json(row) == {
        "code": "TC-MAIL",
        "name": "Mail is removed",
        "prompt": " Write to jan@firma.pl ",
        "model": "corporate-a",
        "run_as_employee_id": str(TEST_EMPLOYEE_ID),
        "expected": "EDIT",
        "expected_policy_code": "RGX-EMAIL",
        "must_not_contain": ["jan@firma.pl"],
        "predefined": True,
        "created_at": "2026-10-04T06:00:00+00:00",
        "updated_at": "2026-10-04T06:00:00+00:00",
    }


def test_missing_codes_name_every_requested_code_not_found():
    found = [Case(**make(EDIT_CASE).model_dump(exclude={"name"}))]
    assert missing_codes(["TC-MAIL", "TC-B", "TC-A"], found) == ["TC-A", "TC-B"]
    assert missing_codes(None, found) == []


@pytest.mark.anyio
@pytest.mark.parametrize("code", ["\x00", "not a code", "tc_x"])
async def test_update_with_an_impossible_code_is_not_found_before_any_query(code):
    # pool=None proves the lookup is refused before the database is touched.
    with pytest.raises(GatewayError) as caught:
        await update_case(None, code, CaseUpdate(name="x"))
    assert (caught.value.status_code, caught.value.code) == (404, "test_case_not_found")


def test_seed_codes_are_unique_and_run_as_the_test_employee():
    codes = [seed.code for seed in SEEDS]
    assert len(codes) == len(set(codes)) > 0
    assert {seed.run_as_employee_id for seed in SEEDS} == {TEST_EMPLOYEE_ID}


def test_every_expected_policy_is_seeded():
    assert {seed.expected_policy_code for seed in SEEDS} - {None} <= set(POLICY_SEEDS)


def prompt_policies() -> set[str]:
    return {code for code, seed in POLICY_SEEDS.items() if seed.applies_to in ("prompt", "both")}


def test_seeds_cover_every_policy_a_prompt_check_can_trip_except_limits():
    covered = {seed.expected_policy_code for seed in SEEDS}
    assert prompt_policies() <= covered
    kinds = {(POLICY_SEEDS[code].kind or "ai", POLICY_SEEDS[code].action) for code in covered - {None}}
    assert kinds == {("authority", "block"), ("regex", "block"), ("regex", "edit"), ("ai", "block")}
    assert any(seed.expected == "ALLOW" for seed in SEEDS)


def seeds_where(predicate) -> list[CaseCreate]:
    return [seed for seed in SEEDS if predicate(seed)]


def non_ai(seed: CaseCreate) -> bool:
    return seed.expected_policy_code is not None and not POLICY_SEEDS[seed.expected_policy_code].ai


def violated(seed: CaseCreate) -> list[str]:
    results = first_stages(SNAPSHOT, TEST_IDENTITY, seed.model, [seed.prompt], {})
    return [entry.code for entry in results if entry.result == "violated"]


def evaluate(seed: CaseCreate, **agent):
    return evaluate_prompt(SNAPSHOT, TEST_IDENTITY, seed.model, [Piece(seed.prompt, True)], {}, **agent)


@pytest.mark.parametrize("seed", seeds_where(lambda s: s.expected == "BLOCK" and non_ai(s)), ids=lambda s: s.code)
def test_block_seeds_are_blocked_by_exactly_their_policy(seed):
    decision = evaluate(seed)
    assert (decision.outcome, decision.responsible) == ("blocked", [seed.expected_policy_code])


@pytest.mark.parametrize("seed", seeds_where(lambda s: s.expected == "EDIT"), ids=lambda s: s.code)
def test_edit_seeds_violate_their_policy_and_are_redacted_by_the_gateway(seed):
    assert violated(seed) == [seed.expected_policy_code]
    assert seed.must_not_contain and all(value in seed.prompt for value in seed.must_not_contain)
    # Even an agent that hands the text back unchanged cannot keep the value in.
    edited = evaluate(seed, verdict=Verdict(status="pass", violations=[], rewritten=[seed.prompt]))
    assert (edited.outcome, edited.responsible) == ("edited", [seed.expected_policy_code])
    assert "[removed]" in edited.rewritten
    assert not any(value in edited.rewritten for value in seed.must_not_contain)


@pytest.mark.parametrize("seed", seeds_where(lambda s: not non_ai(s)), ids=lambda s: s.code)
def test_benign_and_ai_seeds_trip_no_non_ai_policy(seed):
    assert violated(seed) == []
