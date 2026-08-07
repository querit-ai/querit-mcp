"""Environment-backed server configuration."""

from __future__ import annotations

from functools import lru_cache
from typing import Annotated, Literal, cast

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

CsvList = Annotated[list[str], NoDecode]
LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="",
        case_sensitive=False,
        extra="ignore",
    )

    querit_api_base: str = "https://api.querit.ai"
    querit_mcp_free_api_key: SecretStr | None = None
    mcp_free_tier_enabled: bool = False
    redis_url: SecretStr | None = None
    redis_timeout_seconds: float = Field(default=1.0, gt=0, le=10)
    mcp_free_qps_per_ip: int = Field(default=2, ge=1, le=100)
    mcp_free_daily_credits_per_ip: int = Field(default=50, ge=1)
    mcp_free_ip_hmac_secret: SecretStr | None = None
    mcp_free_search_max_count: int = Field(default=5, ge=1)
    mcp_free_contents_max_urls: int = Field(default=3, ge=1, le=10)
    mcp_free_contents_max_chars: int = Field(default=4_000, ge=256)
    mcp_free_crawl_timeout_seconds: int = Field(default=15, ge=1, le=60)

    trusted_proxy_cidrs: CsvList = Field(default_factory=lambda: ["127.0.0.0/8", "::1/128"])
    forwarded_for_header: str = "x-forwarded-for"
    mcp_allowed_hosts: CsvList = Field(
        default_factory=lambda: ["localhost", "localhost:*", "127.0.0.1", "127.0.0.1:*", "testserver"]
    )
    mcp_allowed_origins: CsvList = Field(default_factory=list)
    mcp_enable_dns_rebinding_protection: bool = True

    upstream_connect_timeout_seconds: float = Field(default=5.0, gt=0)
    upstream_timeout_seconds: float = Field(default=75.0, gt=0)
    max_item_content_chars: int = Field(default=8_000, ge=256)
    max_response_chars: int = Field(default=40_000, ge=1_024)
    max_request_body_bytes: int = Field(default=65_536, ge=1_024)
    log_level: LogLevel = "INFO"
    host: str = "127.0.0.1"
    port: int = Field(default=8000, ge=1, le=65_535)

    @field_validator("trusted_proxy_cidrs", "mcp_allowed_hosts", "mcp_allowed_origins", mode="before")
    @classmethod
    def parse_csv_list(cls, value: object) -> object:
        if isinstance(value, str):
            return [part.strip() for part in value.split(",") if part.strip()]
        return value

    @field_validator("querit_api_base")
    @classmethod
    def validate_api_base(cls, value: str) -> str:
        normalized = value.rstrip("/")
        if not normalized.startswith("https://") and not normalized.startswith("http://"):
            raise ValueError("QUERIT_API_BASE must use http or https")
        return normalized

    @field_validator("forwarded_for_header")
    @classmethod
    def normalize_header_name(cls, value: str) -> str:
        normalized = value.strip().lower()
        if not normalized or any(char.isspace() for char in normalized):
            raise ValueError("FORWARDED_FOR_HEADER must be a valid header name")
        return normalized

    @field_validator("log_level")
    @classmethod
    def validate_log_level(cls, value: str) -> LogLevel:
        normalized = value.upper()
        if normalized not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise ValueError("LOG_LEVEL is invalid")
        return cast(LogLevel, normalized)

    @model_validator(mode="after")
    def validate_free_tier(self) -> Settings:
        if not self.mcp_free_tier_enabled:
            return self
        missing = [
            name
            for name, value in (
                ("QUERIT_MCP_FREE_API_KEY", self.querit_mcp_free_api_key),
                ("REDIS_URL", self.redis_url),
                ("MCP_FREE_IP_HMAC_SECRET", self.mcp_free_ip_hmac_secret),
            )
            if value is None or not value.get_secret_value()
        ]
        if missing:
            raise ValueError(f"anonymous free tier requires: {', '.join(missing)}")
        if self.mcp_free_contents_max_chars > self.max_item_content_chars:
            raise ValueError("MCP_FREE_CONTENTS_MAX_CHARS cannot exceed MAX_ITEM_CONTENT_CHARS")
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
