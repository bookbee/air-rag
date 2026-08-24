"""An in-process `VectorStore`: brute-force cosine similarity, no dependency
beyond the standard library.

Correct for a laptop and for tests, and wrong for anything that needs to
survive a restart or scale past one process — the same trade-off
`memory/session.py`'s `InMemorySessionStore` makes in air-platform. A real
backend (Phase 1) implements `retrieval.base.VectorStore` the same way this
does, and `main.py`'s construction is the only place that changes.
"""

from __future__ import annotations

import math
from typing import Any

from air_rag.retrieval.base import VectorHit


def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    # Clamped: floating-point drift can push a genuine near-duplicate a hair
    # past 1.0, and callers (`VectorHit.score`) reject that.
    return max(0.0, min(1.0, dot / (norm_a * norm_b)))


class InMemoryVectorStore:
    def __init__(self) -> None:
        self._items: dict[str, tuple[list[float], str, dict[str, Any]]] = {}

    def __len__(self) -> int:
        return len(self._items)

    async def search(self, embedding: list[float], *, top_k: int) -> list[VectorHit]:
        scored = [
            VectorHit(
                source_id=source_id,
                text=text,
                score=_cosine(embedding, vector),
                metadata=meta,
            )
            for source_id, (vector, text, meta) in self._items.items()
        ]
        scored.sort(key=lambda hit: hit.score, reverse=True)
        return scored[:top_k]

    async def upsert(
        self, source_id: str, embedding: list[float], text: str, metadata: dict[str, Any]
    ) -> None:
        self._items[source_id] = (embedding, text, metadata)
