from functools import lru_cache
from pathlib import Path

from pydantic import AnyHttpUrl, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import URL

ROOT = Path(__file__).resolve().parents[2]  # backend/; only read repo/backend .env files


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(ROOT.parent / ".env", ROOT / ".env"), extra="ignore"
    )
    mysql_host: str = "127.0.0.1"
    mysql_port: int = Field(default=3306, ge=1, le=65535)
    mysql_database: str = "campus_resources"
    mysql_user: str = "campus_app"
    mysql_password: SecretStr
    jwt_signing_key: SecretStr = Field(min_length=32)
    jwt_issuer: str = "campus-resources"
    jwt_audience: str = "campus-web"
    access_token_minutes: int = Field(default=60, ge=1, le=1440)
    booking_max_hours: int = Field(default=4, ge=1, le=24)
    booking_window_days: int = Field(default=7, ge=1, le=31)
    m3_reported_timeout_hours: int = Field(default=24, ge=1, le=8760)
    m3_assigned_timeout_hours: int = Field(default=24, ge=1, le=8760)
    m3_repairing_timeout_hours: int = Field(default=48, ge=1, le=8760)
    m3_acceptance_timeout_hours: int = Field(default=24, ge=1, le=8760)
    m3_timeout_scan_interval_seconds: int = Field(default=60, ge=5, le=3600)
    m3_timeout_scan_enabled: bool = True
    m2_worker_enabled: bool = True
    m2_scan_interval_seconds: int = Field(default=5, ge=1, le=3600)
    m2_reminder_hours: int = Field(default=24, ge=1, le=168)
    m2_resource_base_url: AnyHttpUrl = "http://127.0.0.1:8001"
    m2_resource_timeout_seconds: float = Field(default=5, ge=0.1, le=30)
    resource_service_key: SecretStr | None = Field(default=None, min_length=32)

    @field_validator("resource_service_key")
    @classmethod
    def service_key_is_not_placeholder(cls, value):
        if value is not None and value.get_secret_value().startswith("REPLACE_"):
            raise ValueError("Replace RESOURCE_SERVICE_KEY with an independent random key")
        return value

    @field_validator("jwt_signing_key")
    @classmethod
    def signing_key_is_not_placeholder(cls, value: SecretStr) -> SecretStr:
        if value.get_secret_value().startswith("REPLACE_"):
            raise ValueError("Replace JWT_SIGNING_KEY with a random environment-specific key")
        return value

    @property
    def database_url(self) -> URL:
        return URL.create(
            "mysql+pymysql",
            username=self.mysql_user,
            password=self.mysql_password.get_secret_value(),
            host=self.mysql_host,
            port=self.mysql_port,
            database=self.mysql_database,
            query={"charset": "utf8mb4"},
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
