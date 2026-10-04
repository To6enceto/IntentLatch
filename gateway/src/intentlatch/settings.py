import os
from collections.abc import Mapping
from typing import Literal, Self

from pydantic import BaseModel, Field, SecretStr, ValidationError, model_validator

ENV_VARS = {
    "database_url": "INTENTLATCH_DATABASE_URL",
    "ollama_url": "INTENTLATCH_OLLAMA_URL",
    "model_a": "INTENTLATCH_MODEL_A",
    "model_b": "INTENTLATCH_MODEL_B",
    "token_signing_key": "INTENTLATCH_TOKEN_SIGNING_KEY",
    "admin_api_key": "INTENTLATCH_ADMIN_API_KEY",
    "upstream_timeout_seconds": "INTENTLATCH_UPSTREAM_TIMEOUT_SECONDS",
    "control_agent_url": "INTENTLATCH_CONTROL_AGENT_URL",
    "control_agent_model": "INTENTLATCH_CONTROL_AGENT_MODEL",
    "environment": "INTENTLATCH_ENVIRONMENT",
    "prometheus_url": "INTENTLATCH_PROMETHEUS_URL",
}
REQUIRED = (
    "database_url",
    "ollama_url",
    "model_a",
    "model_b",
    "token_signing_key",
    "admin_api_key",
)
MIN_SECRET_LENGTH = 32


class SettingsError(RuntimeError):
    pass


class Settings(BaseModel):
    database_url: str
    ollama_url: str
    model_a: str
    model_b: str
    token_signing_key: SecretStr = Field(min_length=MIN_SECRET_LENGTH)
    admin_api_key: SecretStr = Field(min_length=MIN_SECRET_LENGTH)
    upstream_timeout_seconds: float = Field(default=120, gt=0)
    control_agent_url: str | None = None
    control_agent_model: str | None = None
    # Production is the default, so a missing setting fails closed.
    environment: Literal["production", "development"] = "production"
    # Optional: only the console's Metrics page reads from it.
    prometheus_url: str | None = None

    @model_validator(mode="after")
    def _control_agent_defaults(self) -> Self:
        # The control agent shares corporate A's model on the same Ollama unless moved.
        if self.control_agent_url is None:
            self.control_agent_url = self.ollama_url
        if self.control_agent_model is None:
            self.control_agent_model = self.model_a
        return self


def load_settings(environ: Mapping[str, str] = os.environ) -> Settings:
    missing = [ENV_VARS[field] for field in REQUIRED if not environ.get(ENV_VARS[field])]
    if missing:
        raise SettingsError(f"Missing required environment variable(s): {', '.join(missing)}")
    values = {field: environ[name] for field, name in ENV_VARS.items() if environ.get(name)}
    try:
        return Settings(**values)
    except ValidationError as exc:
        problems = "; ".join(
            f"{ENV_VARS[str(error['loc'][0])]}: {error['msg']}" for error in exc.errors()
        )
        raise SettingsError(f"Invalid environment variable(s): {problems}") from exc
