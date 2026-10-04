import pytest
from pydantic import ValidationError
from starlette.requests import Request

from intentlatch.auth import required_role
from intentlatch.cli import parser as build_parser
from intentlatch.console_users import (
    LoginRequest,
    allows,
    check_password,
    clean_username,
    hash_password,
    token_hash,
    verify_password,
)
from intentlatch.routes.console import is_https


def test_password_hash_round_trip():
    stored = hash_password("correct horse battery")
    assert stored.startswith("scrypt$16384$8$1$")
    assert verify_password("correct horse battery", stored)
    assert not verify_password("correct horse batterY", stored)


def test_hashes_are_salted():
    assert hash_password("same password!") != hash_password("same password!")


@pytest.mark.parametrize(
    "stored", ["", "plain", "bcrypt$1$2$3$c2FsdA==$aGFzaA==", "scrypt$x$8$1$c2FsdA==$aGFzaA==", "scrypt$16384$8$1$!!$aGFzaA=="]
)
def test_malformed_stored_hash_never_verifies(stored):
    assert not verify_password("anything at all", stored)


@pytest.mark.parametrize(("value", "expected"), [(" ana ", "ana"), ("ana.kowalska@corp", "ana.kowalska@corp"), ("x" * 64, "x" * 64)])
def test_usernames_are_trimmed(value, expected):
    assert clean_username(value) == expected


@pytest.mark.parametrize("value", ["", "   ", "x" * 65, "ana kowalska", "-ana", ".ana", "ana\x00", "ana/root"])
def test_invalid_usernames_are_rejected(value):
    with pytest.raises(ValueError):
        clean_username(value)


def test_password_length_is_enforced():
    assert check_password("x" * 12) == "x" * 12
    assert check_password("x" * 256) == "x" * 256
    for value in ("x" * 11, "x" * 257):
        with pytest.raises(ValueError):
            check_password(value)


@pytest.mark.parametrize(
    ("role", "required", "expected"),
    [
        ("viewer", "viewer", True),
        ("viewer", "analyst", False),
        ("viewer", "admin", False),
        ("analyst", "viewer", True),
        ("analyst", "admin", False),
        ("admin", "viewer", True),
        ("admin", "admin", True),
    ],
)
def test_roles_are_ordered(role, required, expected):
    assert allows(role, required) is expected


@pytest.mark.parametrize(("method", "role"), [("GET", "viewer"), ("HEAD", "viewer"), ("POST", "admin"), ("PATCH", "admin"), ("DELETE", "admin")])
def test_reads_need_viewer_and_writes_need_admin(method, role):
    assert required_role(method) == role


@pytest.mark.parametrize(
    ("method", "path", "role"),
    [
        ("POST", "/admin/test-cases", "analyst"),
        ("PATCH", "/admin/test-cases/TC-X", "analyst"),
        ("GET", "/admin/test-cases", "viewer"),
        ("POST", "/admin/test-runs", "analyst"),
        ("GET", "/admin/reports", "analyst"),
        ("GET", "/admin/reports/items.csv", "analyst"),
        ("GET", "/admin/reportsx", "viewer"),
        ("POST", "/admin/teams", "admin"),
        ("PATCH", "/admin/policies/RGX-X", "admin"),
    ],
)
def test_path_rules_for_test_cases_and_reports(method, path, role):
    assert required_role(method, path=path) == role


def test_login_request_shape():
    assert LoginRequest(username="ana", password="p").password == "p"
    for body in ({"username": "", "password": "p"}, {"username": "ana", "password": ""}, {"username": "ana"}):
        with pytest.raises(ValidationError):
            LoginRequest.model_validate(body)
    with pytest.raises(ValidationError):
        LoginRequest(username="ana", password="p", role="admin")


def test_session_tokens_are_stored_hashed():
    assert len(token_hash("token")) == 32
    assert token_hash("token") != b"token"


def make_request(scheme: str, headers: dict[str, str] | None = None) -> Request:
    raw = [(name.encode(), value.encode()) for name, value in (headers or {}).items()]
    return Request({"type": "http", "scheme": scheme, "method": "POST", "path": "/", "headers": raw, "server": ("x", 80)})


def test_cookie_is_secure_behind_https():
    assert is_https(make_request("https"))
    assert is_https(make_request("http", {"x-forwarded-proto": "https"}))
    assert not is_https(make_request("http"))


def test_cli_requires_a_known_role_and_valid_username():
    parser = build_parser()
    args = parser.parse_args(["console-user", "create", " ana ", "--role", "admin"])
    assert (args.username, args.role) == ("ana", "admin")
    for argv in (["console-user", "create", "ana", "--role", "root"], ["console-user", "create", "a b", "--role", "admin"]):
        with pytest.raises(SystemExit):
            parser.parse_args(argv)
