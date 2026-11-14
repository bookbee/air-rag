"""The vector store boundary.

Kept narrow on purpose — `search` and `upsert`, nothing else — so a real
backend (a managed vector DB, FAISS with a persisted index) is a new class
implementing this Protocol, not a rewrite of `api/v1/retrieval.py`.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol

from pydantic import BaseModel, Field


class VectorHit(BaseModel):
    source_id: str
    text: str
    score: float = Field(ge=0.0, le=1.0)
    metadata: dict[str, Any] = Field(default_factory=dict)


class VectorStore(Protocol):
    async def search(
        self, embedding: list[float], *, top_k: int, where: Mapping[str, str]
    ) -> list[VectorHit]:
        """The `top_k` nearest chunks whose metadata equals every `where`
        pair. Filtering happens before ranking, so a filtered-out chunk
        never takes a top-k slot. Every backend must honour `where`: it is
        how tenant isolation is enforced."""
        ...

    async def upsert(
        self, source_id: str, embedding: list[float], text: str, metadata: dict[str, Any]
    ) -> None: ...

    def __len__(self) -> int: ...
