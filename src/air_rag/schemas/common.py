"""System surfaces: liveness, readiness, capabilities."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from air_rag.constants import DownstreamService


class Principal(BaseModel):
    """The authenticated caller. One caller shape: another AIR service, or an
    operator — no channel concept, unlike air-platform."""

    model_config = ConfigDict(frozen=True)

    key_id: str
    name: str
    #: The only tenant this caller's retrievals search.
    tenant: str
    scopes: frozenset[str] = frozenset()

    def has_scope(self, scope: str) -> bool:
        return scope in self.scopes


class HealthResponse(BaseModel):
    status: str = "ok"
    service: str
    version: str


class DependencyStatus(BaseModel):
    service: DownstreamService
    configured: bool
    reachable: bool | None = None
    latency_ms: float | None = None
    detail: str | None = None


class ReadyResponse(BaseModel):
    """Readiness: can this replica serve a retrieval call at all? Gates on
    air-model-gateway alone — the one dependency without which a query can't be
    embedded."""

    ready: bool
    service: str
    version: str
    checked_at: datetime
    dependencies: list[DependencyStatus] = Field(default_factory=list)
    #: Chunks in the index, across tenants. An empty index is a valid state
    #: (queries honestly return no citations), so it never fails readiness —
    #: it is reported here and in `warnings` for callers and alerts to see.
    document_count: int
    warnings: list[str] = Field(default_factory=list)


class CapabilitiesResponse(BaseModel):
    service: str
    version: str
    env: str
    #: In-process in Phase 0 (retrieval/memory.py) — always `memory` until a
    #: real backend lands.
    vector_store_backend: str
    #: Chunks across every tenant (this route needs `admin:read`).
    document_count: int
    #: The calling key's tenant.
    tenant: str
