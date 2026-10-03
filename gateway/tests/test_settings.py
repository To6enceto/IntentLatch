import pytest

from intentlatch.settings import SettingsError, load_settings

SIGNING_KEY = "s" * 32
ADMIN_KEY = "a" * 32
REQUIRED = {
    "INTENTLATCH_DATABASE_URL": "postgresql://user:secret@localhost:5432/intentlatch",
    "INTENTLATCH_OLLAMA_URL": "http://localhost:11434",
    "INTENTLATCH_MODEL_A": "qwen2.5:3b",
    "INTENTLATCH_MODEL_B": "llama3.2:1b",
    "INTENTLATCH_TOKEN_SIGNING_KEY": SIGNING_KEY,
    "INTENTLATCH_ADMIN_API_KEY": ADMIN_KEY,
}


def test_upstream_timeout_defaults_to_120_seconds():
    assert load_settings(REQUIRED).upstream_timeout_seconds == 120


@pytest.mark.parametrize(
    "name", ["INTENTLATCH_OLLAMA_URL", "INTENTLATCH_TOKEN_SIGNING_KEY", "INTENTLATCH_ADMIN_API_KEY"]
)
def test_missing_required_variable_is_named(name):
    environ = {key: value for key, value in REQUIRED.items() if key != name}
    with pytest.raises(SettingsError, match=name):
        load_settings(environ)


def test_non_positive_timeout_is_rejected_and_named():
    with pytest.raises(SettingsError, match="INTENTLATCH_UPSTREAM_TIMEOUT_SECONDS"):
        load_settings(REQUIRED | {"INTENTLATCH_UPSTREAM_TIMEOUT_SECONDS": "0"})


@pytest.mark.parametrize("name", ["INTENTLATCH_TOKEN_SIGNING_KEY", "INTENTLATCH_ADMIN_API_KEY"])
def test_short_secret_is_rejected_and_named_without_echoing_it(name):
    short = "x" * 31
    with pytest.raises(SettingsError, match=name) as raised:
        load_settings(REQUIRED | {name: short})
    assert short not in str(raised.value)


def test_secrets_are_hidden_from_repr():
    settings = load_settings(REQUIRED)
    assert SIGNING_KEY not in repr(settings)
    assert ADMIN_KEY not in repr(settings)
    assert settings.token_signing_key.get_secret_value() == SIGNING_KEY
