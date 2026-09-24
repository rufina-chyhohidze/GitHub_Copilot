"""Validate configuration early without exposing credentials in error messages."""

from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="COPILOT_", env_file=PROJECT_ROOT / ".env", extra="ignore"
    )

    database_url: SecretStr | None = None
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    model_id: str | None = None
    embedding_model_id: str | None = None
    embedding_dimensions: int | None = Field(default=None, gt=0)
    provider_api_key: SecretStr | None = None
    max_tool_calls: int = Field(default=12, gt=0, le=100)
    max_context_tokens: int = Field(default=16000, gt=0)
    max_output_tokens: int = Field(default=2000, gt=0)
    run_timeout_seconds: int = Field(default=90, gt=0)

    @field_validator("database_url")
    @classmethod
    def validate_database_url(cls, value: SecretStr | None) -> SecretStr | None:
        if value is None:
            return value
        try:
            url = make_url(value.get_secret_value())
            if url.drivername != "postgresql+psycopg" or not url.database:
                raise ValueError
        except Exception:
            raise ValueError("Use a postgresql+psycopg URL with a database name") from None
        return value

    def require_database_url(self) -> str:
        if self.database_url is None:
            raise ValueError("Set COPILOT_DATABASE_URL in .env; see .env.example")
        return self.database_url.get_secret_value()

    def require_provider(self) -> None:
        required = ("model_id", "embedding_model_id", "embedding_dimensions", "provider_api_key")
        missing = [f"COPILOT_{name.upper()}" for name in required if not getattr(self, name)]
        if missing:
            raise ValueError("Set provider configuration: " + ", ".join(missing))
