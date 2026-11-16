"""The retrieval contract air-platform's GATHER step calls
(air-platform/docs/01-hld.md §4: "air-rag-service — retrieval + citations")."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator


class RetrievalRequest(BaseModel):
    #: Unknown fields are a 422, so a caller sending e.g. `tenant` learns
    #: it isn't honoured instead of silently searching its key's tenant.
    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1, max_length=4000)
    #: Strict: `true`, `"5"` and `5.0` are a 422, not silently 1 or 5 (SIT RG-002).
    top_k: StrictInt = Field(default=5, ge=1, le=50)
    #: Tenant/user scoping for a future multi-tenant index — accepted now so
    #: the wire contract doesn't change when that lands. Capped like every
    #: other caller-supplied identifier, since it is not used yet and must
    #: not become a free megabyte per request (SIT RG-003).
    user_id: str | None = Field(default=None, max_length=256)

    @field_validator("query")
    @classmethod
    def _query_has_content(cls, value: str) -> str:
        """A blank query embeds to a vector like any other and comes back with
        match-level scores for nothing (SIT RG-003), so it is refused here."""
        if not value.strip():
            raise ValueError("must contain more than whitespace")
        return value


class Citation(BaseModel):
    source_id: str
    snippet: str
    score: float = Field(ge=0.0, le=1.0)
    metadata: dict[str, Any] = Field(default_factory=dict)


class RetrievalResponse(BaseModel):
    query: str
    citations: list[Citation] = Field(default_factory=list)
