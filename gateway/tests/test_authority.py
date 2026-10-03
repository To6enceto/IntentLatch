import uuid

import pytest
from pydantic import ValidationError

from intentlatch.identity import Identity
from intentlatch.routes.authority import AuthorityRequest, invalid_body, valid_body

IDENTITY = Identity(
    employee_id=uuid.UUID("11111111-1111-4111-8111-111111111111"),
    employee_name="Ana",
    team_id=uuid.UUID("33333333-3333-4333-8333-333333333333"),
    team_name="payments",
    authorized_models=["corporate-a"],
)


def test_valid_body_reports_identity_and_current_models():
    assert valid_body(IDENTITY) == {
        "valid": True,
        "employee": {"id": "11111111-1111-4111-8111-111111111111", "name": "Ana"},
        "team": {"id": "33333333-3333-4333-8333-333333333333", "name": "payments"},
        "authorized_models": ["corporate-a"],
    }


@pytest.mark.parametrize("reason", ["token_invalid", "token_revoked"])
def test_invalid_body_reveals_only_validity_and_reason(reason):
    assert invalid_body(reason) == {"valid": False, "reason": reason}


def test_request_accepts_a_token():
    assert AuthorityRequest(token="abc.def.ghi").token == "abc.def.ghi"


@pytest.mark.parametrize("body", [{}, {"token": ""}, {"token": 123}, {"token": None}])
def test_request_rejects_missing_empty_or_non_string_token(body):
    with pytest.raises(ValidationError):
        AuthorityRequest(**body)


def test_request_rejects_unknown_fields():
    with pytest.raises(ValidationError):
        AuthorityRequest(token="abc", employee_id="x")
