import pytest
from pydantic import ValidationError

from intentlatch.teams import EmployeeCreate, TeamCreate, TeamUpdate


def test_team_name_is_trimmed():
    assert TeamCreate(name="  Payments  ", authorized_models=["corporate-a"]).name == "Payments"


@pytest.mark.parametrize("name", ["", "   ", "x" * 65])
def test_team_name_length_is_enforced(name):
    with pytest.raises(ValidationError):
        TeamCreate(name=name, authorized_models=["corporate-a"])


def test_team_name_at_limit_is_accepted():
    assert TeamCreate(name="x" * 64, authorized_models=["corporate-a"]).name == "x" * 64


def test_models_are_deduplicated_and_ordered():
    team = TeamCreate(name="t", authorized_models=["corporate-b", "corporate-a", "corporate-b"])
    assert team.authorized_models == ["corporate-a", "corporate-b"]


@pytest.mark.parametrize("models", [[], ["gpt-4"], ["corporate-a", "corporate-c"]])
def test_empty_or_unknown_models_are_rejected(models):
    with pytest.raises(ValidationError):
        TeamCreate(name="t", authorized_models=models)
    with pytest.raises(ValidationError):
        TeamUpdate(authorized_models=models)


def test_unknown_fields_are_rejected():
    with pytest.raises(ValidationError):
        TeamCreate(name="t", authorized_models=["corporate-a"], owner="x")
    with pytest.raises(ValidationError):
        TeamUpdate(authorized_models=["corporate-a"], name="renamed")
    with pytest.raises(ValidationError):
        EmployeeCreate(name="Ana", team_id="x")


def test_employee_name_is_trimmed_and_length_checked():
    assert EmployeeCreate(name=" Ana ").name == "Ana"
    assert EmployeeCreate(name="x" * 128).name == "x" * 128
    with pytest.raises(ValidationError):
        EmployeeCreate(name="x" * 129)
    with pytest.raises(ValidationError):
        EmployeeCreate(name=" ")


def test_nul_in_a_name_is_rejected():
    with pytest.raises(ValidationError, match="NUL"):
        TeamCreate(name="nul\x00team", authorized_models=["corporate-a"])
    with pytest.raises(ValidationError, match="NUL"):
        EmployeeCreate(name="nul\x00emp")


def test_non_string_name_is_rejected():
    with pytest.raises(ValidationError):
        TeamCreate(name=42, authorized_models=["corporate-a"])
