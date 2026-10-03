import os
from collections.abc import Mapping

from pydantic import BaseModel, Field, ValidationError

ENV_VARS = {
    "database_url": "INTENTLATCH_DATABASE_URL",
    "ollama_url": "INTENTLATCH_OLLAMA_URL",
    "model_a": "INTENTLATCH_MODEL_A",
    "model_b": "INTENTLATCH_MODEL_B",
    "upstream_timeout_seconds": "INTENTLATCH_UPSTREAM_TIMEOUT_SECONDS",
}
REQUIRED = ("database_url", "ollama_url", "model_a", "model_b")


class SettingsError(RuntimeError):
    pass


class Settings(BaseModel):
    database_url: str
    ollama_url: str
    model_a: str
    model_b: str
    upstream_timeout_seconds: float = Field(default=120, gt=0)


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
