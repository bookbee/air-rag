"""Application settings.

12-factor: every value is settable from the environment with the prefix
`AIR_RAG__` and `__` as the nesting delimiter, e.g.
`AIR_RAG__MODEL_GATEWAY__BASE_URL=http://localhost:8083`.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal, Self

from pydantic import BaseModel, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from air_rag.constants import TENANT_PATTERN

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


# ── air-model-gateway ─────────────────────────────────────────────────────────


class ModelGatewaySettings(BaseModel):
    """air-model-gateway. Embeddings — ingestion and query-time both — go
    through this. The one dependency whose absence is fatal, so `/v1/ready`
    gates on it and on nothing else."""

    base_url: str = "http://localhost:8083"
    #: This service's gateway token (`air-rag-dev` on a laptop).
    api_key: SecretStr | None = None
    timeout_s: float = Field(default=30.0, gt=0)
    health_timeout_s: float = Field(default=2.0, gt=0)
    max_retries: int = Field(default=2, ge=0, le=8)
    #: A published embedding name — this index's identity. Changing it means
    #: a re-index; vectors from different names are not comparable.
    embed_model: str = "local-768-v1"
    #: Checked against every gateway response and at readiness.
    embed_dimensions: int = Field(default=768, ge=1)
    #: Texts per `/v1/embed` call; the gateway rejects more than 2,048.
    embed_batch_size: int = Field(default=256, ge=1, le=2048)


# ── Retrieval ─────────────────────────────────────────────────────────────────


class RetrievalSettings(BaseModel):
    """Phase 0 ships an in-process vector store (retrieval/memory.py) — fine
    for a laptop and tests, gone on restart. A real backend is Phase 1."""

    default_top_k: int = Field(default=5, ge=1, le=50)


# ── Ingestion ─────────────────────────────────────────────────────────────────


class IngestionSettings(BaseModel):
    """Startup seeding: the in-process index can only be filled from inside
    the process, and the public API stays read-only, so a seed corpus (SIT's
    `sit/corpus/`) is ingested at boot. Off unless `seed_dir` is set."""

    seed_dir: Path | None = None
    chunk_size: int = Field(default=800, ge=100, le=32_000)

    @field_validator("seed_dir", mode="before")
    @classmethod
    def _blank_is_unset(cls, value: object) -> object:
        # Compose passes `${AIR_RAG_SEED_DIR:-}` through as "" when unset.
        return None if value == "" else value


# ── Security ──────────────────────────────────────────────────────────────────


class SecuritySettings(BaseModel):
    api_keys_inline: str | None = None
    api_keys_file: Path | None = None
    allow_unauthenticated: bool = False
    #: The tenant an unauthenticated caller searches; required when
    #: `allow_unauthenticated` is on — there is no cross-tenant fallback.
    anonymous_tenant: str | None = Field(default=None, pattern=TENANT_PATTERN)
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
    model_gateway: ModelGatewaySettings = Field(default_factory=ModelGatewaySettings)
    retrieval: RetrievalSettings = Field(default_factory=RetrievalSettings)
    ingestion: IngestionSettings = Field(default_factory=IngestionSettings)
    security: SecuritySettings = Field(default_factory=SecuritySettings)
    obs: ObservabilitySettings = Field(default_factory=ObservabilitySettings)

    @model_validator(mode="after")
    def _guard_production(self) -> Self:
        if self.app.is_production and self.security.allow_unauthenticated:
            raise ValueError(
                "security.allow_unauthenticated must be false when app.env is production"
            )
        if self.security.allow_unauthenticated and self.security.anonymous_tenant is None:
            raise ValueError(
                "security.anonymous_tenant is required when security.allow_unauthenticated is true"
            )
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
