import pytest
from pydantic import ValidationError

from intentlatch.errors import GatewayError, describe_validation_error
from intentlatch.policies import PolicyCreate, PolicyUpdate, load_seeds, merge_update, update_policy

AUTHORITY = {"code": "AUTH-X", "ai": False, "kind": "authority", "action": "block", "enabled": True}
LIMIT = {
    "code": "LIM-X",
    "ai": False,
    "kind": "limit",
    "params": {"max_tokens": 1000, "window_seconds": 3600, "team": None},
    "action": "block",
    "enabled": True,
}
REGEX = {
    "code": "RGX-X",
    "ai": False,
    "kind": "regex",
    "params": {"pattern": "(?i)iban"},
    "action": "edit",
    "applies_to": "both",
    "enabled": True,
}
AI = {"code": "AI-X", "ai": True, "text": "No credentials.", "action": "block", "applies_to": "prompt", "enabled": True}


def make(base: dict, **changes) -> PolicyCreate:
    return PolicyCreate.model_validate({**base, **changes})


def rejected(base: dict, match: str | None = None, **changes) -> None:
    with pytest.raises(ValidationError, match=match):
        make(base, **changes)


def without(base: dict, key: str) -> dict:
    return {name: value for name, value in base.items() if name != key}


@pytest.mark.parametrize(
    ("value", "expected"),
    [("rgx-iban", "RGX-IBAN"), ("  AI-NO-CREDENTIALS ", "AI-NO-CREDENTIALS"), ("A1", "A1"), ("x" * 64, "X" * 64)],
)
def test_code_is_trimmed_and_uppercased(value, expected):
    assert make(AUTHORITY, code=value).code == expected


@pytest.mark.parametrize(
    "value", ["", "A", "x" * 65, "1ABC", "-ABC", "ABC-", "A--B", "A_B", "A B", "ſTUFF", 42]
)
def test_invalid_codes_are_rejected(value):
    rejected(AUTHORITY, code=value)


def test_ai_policy_text_is_trimmed_and_bounded():
    assert make(AI, text="  No credentials.  ").text == "No credentials."
    assert make(AI, text="x" * 1000).text == "x" * 1000
    rejected(AI, text="x" * 1001)
    rejected(AI, text="   ")


def test_ai_policy_shape():
    rejected(without(AI, "text"), match="text is required when ai is true")
    rejected(AI, kind="regex", match="kind is only allowed when ai is false")
    rejected(AI, params={"pattern": "x"}, match="params must be empty for AI policies")
    rejected(without(AI, "applies_to"), match="applies_to is required for AI policies")
    assert make(AI, params={}).params == {}
    assert make(AI, action="edit").action == "edit"


def test_non_ai_policy_shape():
    rejected(AUTHORITY, text="No credentials.", match="text is only allowed when ai is true")
    rejected(without(AUTHORITY, "kind"), match="kind is required when ai is false")
    rejected(AUTHORITY, kind="budget")


def test_authority_policy_is_prompt_only_and_blocking():
    policy = make(AUTHORITY)
    assert (policy.params, policy.applies_to) == ({}, "prompt")
    assert make(AUTHORITY, applies_to="prompt", params={}).applies_to == "prompt"
    rejected(AUTHORITY, params={"team": None}, match="params must be empty for authority policies")
    rejected(AUTHORITY, action="edit", match="action must be block for authority policies")
    for applies_to in ("response", "both"):
        rejected(AUTHORITY, applies_to=applies_to, match="applies_to must be prompt for authority policies")


def test_limit_params_accept_bounds():
    params = {"max_tokens": 1, "window_seconds": 2_147_483_647, "team": "  Payments "}
    policy = make(LIMIT, params=params)
    assert policy.params == {"max_tokens": 1, "window_seconds": 2_147_483_647, "team": "Payments"}
    assert policy.applies_to == "prompt"
    assert make(LIMIT).params["team"] is None


@pytest.mark.parametrize("value", [0, -1, 2_147_483_648, True, "10", 10.0])
@pytest.mark.parametrize("field", ["max_tokens", "window_seconds"])
def test_limit_numbers_are_strict_positive_integers(field, value):
    rejected(LIMIT, params={**LIMIT["params"], field: value}, match=f"params.{field}")


def test_limit_params_shape():
    rejected(LIMIT, params=without(LIMIT["params"], "team"), match="params.team: Field required")
    rejected(LIMIT, params={**LIMIT["params"], "team": "  "}, match="params.team")
    rejected(LIMIT, params={**LIMIT["params"], "scope": "all"}, match="params.scope")
    rejected(without(LIMIT, "params"), match="params.max_tokens: Field required")
    rejected(LIMIT, action="edit", match="action must be block for limit policies")
    rejected(LIMIT, applies_to="both", match="applies_to must be prompt for limit policies")


def test_regex_params_shape():
    assert make(REGEX).params == {"pattern": "(?i)iban"}
    assert make(REGEX, action="block").action == "block"
    assert make(REGEX, params={"pattern": "x" * 1000}).params["pattern"] == "x" * 1000
    rejected(without(REGEX, "params"), match="params.pattern: Field required")
    rejected(REGEX, params={"pattern": ""}, match="params.pattern")
    rejected(REGEX, params={"pattern": "x" * 1001}, match="params.pattern")
    rejected(REGEX, params={"pattern": "("}, match="params.pattern: .*invalid regular expression")
    rejected(REGEX, params={"pattern": "x", "flags": "i"}, match="params.flags")
    rejected(without(REGEX, "applies_to"), match="applies_to is required for regex policies")


def test_regex_error_message_with_braces_is_kept_verbatim():
    with pytest.raises(ValidationError) as caught:
        make(REGEX, params={"pattern": "(?P<a{>x)"})
    assert "group name 'a{'" in describe_validation_error(caught.value)


@pytest.mark.parametrize("field", ["ai", "enabled"])
def test_booleans_are_required_and_strict(field):
    rejected(without(AUTHORITY, field))
    rejected(AUTHORITY, **{field: "true"})
    rejected(AUTHORITY, **{field: 1})


def test_unknown_fields_are_rejected():
    rejected(AUTHORITY, owner="x")
    rejected(AUTHORITY, action="allow")
    rejected(REGEX, applies_to="request")


def test_update_requires_a_known_field():
    with pytest.raises(ValidationError, match="at least one of"):
        PolicyUpdate.model_validate({})
    for field in ("code", "ai", "kind", "owner"):
        with pytest.raises(ValidationError, match=field):
            PolicyUpdate.model_validate({field: "x"})


def stored(base: dict) -> dict:
    return make(base).model_dump()


def test_update_keeps_unspecified_fields():
    merged = merge_update(stored(REGEX), PolicyUpdate(enabled=False))
    assert merged.model_dump() == {**stored(REGEX), "enabled": False}


def test_update_replaces_params_whole():
    current = stored(LIMIT)
    with pytest.raises(ValidationError, match="params.window_seconds: Field required"):
        merge_update(current, PolicyUpdate(params={"max_tokens": 5, "team": None}))
    merged = merge_update(current, PolicyUpdate(params={"max_tokens": 5, "window_seconds": 60, "team": None}))
    assert merged.params == {"max_tokens": 5, "window_seconds": 60, "team": None}


@pytest.mark.parametrize(
    ("base", "update", "match"),
    [
        (AUTHORITY, {"action": "edit"}, "action must be block for authority policies"),
        (REGEX, {"text": "No credentials."}, "text is only allowed when ai is true"),
        (AI, {"text": None}, "text is required when ai is true"),
        (LIMIT, {"applies_to": "response"}, "applies_to must be prompt for limit policies"),
        (AI, {"params": {"pattern": "x"}}, "params must be empty for AI policies"),
        (REGEX, {"enabled": None}, "enabled"),
    ],
)
def test_update_is_revalidated_after_merging(base, update, match):
    with pytest.raises(ValidationError, match=match):
        merge_update(stored(base), PolicyUpdate.model_validate(update))


def test_nul_characters_are_validation_errors():
    with pytest.raises(ValidationError) as caught:
        make(AI, text="no\x00creds")
    assert describe_validation_error(caught.value) == "text: Value error, must not contain NUL characters"
    rejected(REGEX, params={"pattern": "a\x00b"}, match="params.pattern: .*NUL")
    rejected(LIMIT, params={**LIMIT["params"], "team": "pay\x00"}, match="params.team: .*NUL")
    rejected(AUTHORITY, code="AB\x00")


@pytest.mark.anyio
@pytest.mark.parametrize("code", ["\x00", "not a code", "ab_c"])
async def test_update_with_an_impossible_code_is_not_found_before_any_query(code):
    # pool=None proves the lookup is refused before the database is touched.
    with pytest.raises(GatewayError) as caught:
        await update_policy(None, code, PolicyUpdate(enabled=False))
    assert (caught.value.status_code, caught.value.code) == (404, "policy_not_found")


def test_seed_file_is_valid():
    seeds = load_seeds()
    codes = [seed.code for seed in seeds]
    assert len(codes) == len(set(codes))
    authority = next(seed for seed in seeds if seed.code == "AUTH-MODEL")
    assert (authority.ai, authority.kind, authority.action, authority.enabled) == (False, "authority", "block", True)


def test_validation_errors_are_described_with_their_field():
    with pytest.raises(ValidationError) as caught:
        make(REGEX, params={"pattern": "("})
    assert describe_validation_error(caught.value).startswith("params.pattern: ")
    with pytest.raises(ValidationError) as caught:
        make(AUTHORITY, code="1")
    assert describe_validation_error(caught.value).startswith("code: ")
