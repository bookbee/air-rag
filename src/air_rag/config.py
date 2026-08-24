"""Application settings.

12-factor: every value is settable from the environment with the prefix
`AIR_RAG__` and `__` as the nesting delimiter, e.g.
`AIR_RAG__LLM__BASE_URL=http://localhost:8083`.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal, Self

from pydantic import BaseModel, Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# ── Application ───────────────────────────────────────────────────────────────


class AppSettings(BaseModel):
    env: Literal["development", "staging", "production", "test"] = "development"
    host: str = "0.0.0.0"  # noqa: S104 — containers bind all interfaces by design
    #: 8087 is this service's slot in the AIR port map (air-infra/README.md).
    port: int = Field(default=8087, ge=1, le=65535)
    docs_enabled: bool = True
    max_body_bytes: int = Field(default=1_048_576, ge=1024)

    @property
    def is_production(self) -> bool:
        return self.env == "production"


# ── air-llm ───────────────────────────────────────────────────────────────────


class LlmSettings(BaseModel):
    """The air-llm gateway. Embeddings — ingestion and query-time both — go
    through this. The one dependency whose absence is fatal, so `/v1/ready`
    gates on it and on nothing else, same shape as air-tools'/air-platform's
    `LlmSettings`."""

    base_url: str = "http://localhost:8083"
    api_key: SecretStr | None = None
    timeout_s: float = Field(default=30.0, gt=0)
    health_timeout_s: float = Field(default=2.0, gt=0)
    max_retries: int = Field(default=2, ge=0, le=8)
    default_model: str | None = None

    @property
    def configured(self) -> bool:
        return self.api_key is not None


# ── Retrieval ─────────────────────────────────────────────────────────────────


class RetrievalSettings(BaseModel):
    """Phase 0 ships an in-process vector store (retrieval/memory.py) — fine
    for a laptop and tests, gone on restart. A real backend is Phase 1."""

    default_top_k: int = Field(default=5, ge=1, le=50)


# ── Security ──────────────────────────────────────────────────────────────────


class SecuritySettings(BaseModel):
    api_keys_inline: str | None = None
    api_keys_file: Path | None = None
    allow_unauthenticated: bool = False
    hash_salt: SecretStr = SecretStr("air-rag-dev-salt")
    default_rate_limit_rpm: int = Field(default=120, ge=1)
    default_burst: int = Field(default=20, ge=1)


# ── Observability ─────────────────────────────────────────────────────────────


class ObservabilitySettings(BaseModel):
    service_name: str = "air-rag"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    log_format: Literal["json", "console"] = "json"
    metrics_enabled: bool = True
    metrics_path: str = "/metrics"


# ── Root ──────────────────────────────────────────────────────────────────────


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="AIR_RAG__",
        env_nested_delimiter="__",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    app: AppSettings = Field(default_factory=AppSettings)
    llm: LlmSettings = Field(default_factory=LlmSettings)
    retrieval: RetrievalSettings = Field(default_factory=RetrievalSettings)
    security: SecuritySettings = Field(default_factory=SecuritySettings)
    obs: ObservabilitySettings = Field(default_factory=ObservabilitySettings)

    @model_validator(mode="after")
    def _guard_production(self) -> Self:
        if self.app.is_production and self.security.allow_unauthenticated:
            raise ValueError(
                "security.allow_unauthenticated must be false when app.env is production"
            )
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
