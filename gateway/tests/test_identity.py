import uuid
from datetime import UTC, datetime, timedelta

import jwt
import pytest

from intentlatch.identity import (
    EmployeeRecord,
    TokenClaims,
    TokenError,
    bearer_token,
    issue_token,
    match_identity,
    verify_token,
)

KEY = "k" * 32
OTHER_KEY = "o" * 32
EMPLOYEE_ID = uuid.UUID("11111111-1111-4111-8111-111111111111")
TOKEN_ID = uuid.UUID("22222222-2222-4222-8222-222222222222")
TEAM_ID = uuid.UUID("33333333-3333-4333-8333-333333333333")
ISSUED_AT = datetime(2026, 10, 3, 18, 0, tzinfo=UTC)


def make_token(key: str = KEY) -> str:
    return issue_token(
        employee_id=EMPLOYEE_ID,
        team="payments",
        models=["corporate-a"],
        token_id=TOKEN_ID,
        issued_at=ISSUED_AT,
        key=key,
    )


def record(**changes) -> EmployeeRecord:
    values = dict(
        employee_id=EMPLOYEE_ID,
        employee_name="Ana",
        token_id=TOKEN_ID,
        revoked_at=None,
        team_id=TEAM_ID,
        team_name="payments",
        authorized_models=["corporate-a", "corporate-b"],
    )
    return EmployeeRecord(**(values | changes))


def assert_invalid(token: str) -> None:
    with pytest.raises(TokenError) as raised:
        verify_token(token, KEY)
    assert raised.value.code == "token_invalid"


def test_token_round_trip_carries_claims():
    token = make_token()
    assert verify_token(token, KEY) == TokenClaims(employee_id=EMPLOYEE_ID, token_id=TOKEN_ID)
    payload = jwt.decode(token, KEY, algorithms=["HS256"])
    assert payload == {
        "sub": str(EMPLOYEE_ID),
        "team": "payments",
        "models": ["corporate-a"],
        "jti": str(TOKEN_ID),
        "iat": int(ISSUED_AT.timestamp()),
    }


def test_an_expiry_adds_exp_and_is_enforced():
    now = datetime.now(UTC)
    live = issue_token(
        employee_id=EMPLOYEE_ID, team="payments", models=["corporate-a"], token_id=TOKEN_ID,
        issued_at=now, key=KEY, expires_at=now + timedelta(seconds=60),
    )
    assert verify_token(live, KEY) == TokenClaims(employee_id=EMPLOYEE_ID, token_id=TOKEN_ID)
    assert jwt.decode(live, KEY, algorithms=["HS256"])["exp"] == int((now + timedelta(seconds=60)).timestamp())
    expired = issue_token(
        employee_id=EMPLOYEE_ID, team="payments", models=["corporate-a"], token_id=TOKEN_ID,
        issued_at=now - timedelta(seconds=120), key=KEY, expires_at=now - timedelta(seconds=60),
    )
    assert_invalid(expired)


def test_tampered_payload_is_rejected():
    header, payload, signature = make_token().split(".")
    forged = jwt.encode({"sub": str(uuid.uuid4()), "jti": str(TOKEN_ID), "iat": 1}, OTHER_KEY).split(".")[1]
    assert_invalid(".".join([header, forged, signature]))


def test_token_signed_with_another_key_is_rejected():
    assert_invalid(make_token(OTHER_KEY))


def test_alg_none_is_rejected():
    payload = {"sub": str(EMPLOYEE_ID), "jti": str(TOKEN_ID), "iat": 1}
    assert_invalid(jwt.encode(payload, None, algorithm="none"))


def test_other_hmac_algorithm_is_rejected():
    payload = {"sub": str(EMPLOYEE_ID), "jti": str(TOKEN_ID), "iat": 1}
    assert_invalid(jwt.encode(payload, KEY + KEY, algorithm="HS512"))


def test_missing_jti_is_rejected():
    assert_invalid(jwt.encode({"sub": str(EMPLOYEE_ID), "iat": 1}, KEY, algorithm="HS256"))


def test_non_uuid_subject_is_rejected():
    assert_invalid(jwt.encode({"sub": "ana", "jti": str(TOKEN_ID), "iat": 1}, KEY, algorithm="HS256"))


def test_garbage_is_rejected():
    assert_invalid("not-a-token")


@pytest.mark.parametrize("header", [None, "", "Bearer", "Bearer   ", "Basic abc", "Token abc"])
def test_bearer_token_missing_or_wrong_scheme(header):
    with pytest.raises(TokenError) as raised:
        bearer_token(header)
    assert raised.value.code == "token_missing"


@pytest.mark.parametrize("header", ["Bearer abc.def", "bearer abc.def", "  Bearer   abc.def  "])
def test_bearer_token_valid(header):
    assert bearer_token(header) == "abc.def"


def test_match_identity_returns_database_authority():
    identity = match_identity(TokenClaims(EMPLOYEE_ID, TOKEN_ID), record())
    assert identity.team_name == "payments"
    assert identity.authorized_models == ["corporate-a", "corporate-b"]


def test_match_identity_unknown_employee_is_invalid():
    with pytest.raises(TokenError) as raised:
        match_identity(TokenClaims(EMPLOYEE_ID, TOKEN_ID), None)
    assert raised.value.code == "token_invalid"


@pytest.mark.parametrize(
    "changes", [{"token_id": uuid.uuid4()}, {"revoked_at": datetime(2026, 10, 3, tzinfo=UTC)}]
)
def test_match_identity_reissued_or_revoked_is_revoked(changes):
    with pytest.raises(TokenError) as raised:
        match_identity(TokenClaims(EMPLOYEE_ID, TOKEN_ID), record(**changes))
    assert raised.value.code == "token_revoked"
