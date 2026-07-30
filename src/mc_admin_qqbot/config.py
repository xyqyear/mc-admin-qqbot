from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, HttpUrl, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="MC_ADMIN_QQBOT_",
        env_file=".env.bot",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    qq_bot_id: int = Field(gt=0)
    qq_group_id: int = Field(gt=0)
    onebot_access_token: SecretStr = Field(min_length=1)
    mc_admin_base_url: HttpUrl
    mc_admin_master_token: SecretStr = Field(min_length=1)
    superusers: set[int] = Field(default_factory=set)
    excluded_servers: set[str] = Field(default_factory=set)
    default_server: str | None = None
    database_url: str = "sqlite+aiosqlite:///./data/bot.db"
    restart_timeout_seconds: float = Field(default=600, gt=0)
    restart_poll_interval_seconds: float = Field(default=5, gt=0)
    host: str = "0.0.0.0"
    port: int = Field(default=8080, ge=1, le=65535)
    log_level: Literal["TRACE", "DEBUG", "INFO", "SUCCESS", "WARNING", "ERROR"] = "INFO"

    @field_validator("mc_admin_base_url")
    @classmethod
    def validate_api_url(cls, value: HttpUrl) -> HttpUrl:
        if (value.path or "").rstrip("/") != "/api":
            raise ValueError("mc_admin_base_url must end with /api")
        return value

    @field_validator("database_url")
    @classmethod
    def validate_database_url(cls, value: str) -> str:
        url = make_url(value)
        if url.drivername != "sqlite+aiosqlite":
            raise ValueError("v1 only supports sqlite+aiosqlite database URLs")
        if not url.database or url.database == ":memory:" or url.query:
            raise ValueError(
                "database_url must reference a SQLite file without query parameters"
            )
        return value

    @field_validator("excluded_servers")
    @classmethod
    def validate_server_ids(cls, value: set[str]) -> set[str]:
        if any(not server or server != server.strip() for server in value):
            raise ValueError("excluded server IDs must be non-empty and trimmed")
        return value

    @model_validator(mode="after")
    def validate_defaults(self) -> "Settings":
        if any(user_id <= 0 for user_id in self.superusers):
            raise ValueError("superuser IDs must be positive")
        if self.default_server is not None and (
            not self.default_server
            or self.default_server != self.default_server.strip()
        ):
            raise ValueError("default_server must be non-empty and trimmed")
        if self.default_server in self.excluded_servers:
            raise ValueError("default_server cannot be excluded")
        return self

    @property
    def mc_admin_api_url(self) -> str:
        return str(self.mc_admin_base_url).rstrip("/")

    @property
    def database_path(self) -> Path:
        database = make_url(self.database_url).database
        if database is None:
            raise RuntimeError("Validated database URL has no path")
        return Path(database)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()  # pyright: ignore[reportCallIssue]
