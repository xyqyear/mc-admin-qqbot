from pathlib import Path
from typing import Literal

from pydantic import Field, HttpUrl, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="MC_ADMIN_QQBOT_", env_file=".env.bot", extra="ignore"
    )

    qq_app_id: str = Field(min_length=1)
    qq_app_secret: SecretStr = Field(min_length=1)
    mc_admin_base_url: HttpUrl
    mc_admin_master_token: SecretStr = Field(min_length=1)
    default_server: str | None = None
    database_url: str = "sqlite+aiosqlite:///./data/official-bot.db"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"

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
            raise ValueError("Only sqlite+aiosqlite is supported")
        if not url.database or url.database == ":memory:" or url.query:
            raise ValueError("Use a SQLite file without query parameters")
        return value

    @property
    def mc_admin_api_url(self) -> str:
        return str(self.mc_admin_base_url).rstrip("/")

    @property
    def database_path(self) -> Path:
        return Path(str(make_url(self.database_url).database))
