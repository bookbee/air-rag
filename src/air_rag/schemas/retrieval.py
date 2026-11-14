"""The retrieval contract air-platform's GATHER step calls
(air-platform/docs/01-hld.md §4: "air-rag — retrieval + citations")."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class RetrievalRequest(BaseModel):
    #: Unknown fields are a 422, so a caller sending e.g. `tenant` learns
    #: it isn't honoured instead of silently searching its key's tenant.
    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1, max_length=4000)
    top_k: int = Field(default=5, ge=1, le=50)
    #: Tenant/user scoping for a future multi-tenant index — accepted now so
    #: the wire contract doesn't change when that lands.
    user_id: str | None = None


class Citation(BaseModel):
    source_id: str
    snippet: str
    score: float = Field(ge=0.0, le=1.0)
    metadata: dict[str, Any] = Field(default_factory=dict)


class RetrievalResponse(BaseModel):
    query: str
    citations: list[Citation] = Field(default_factory=list)
