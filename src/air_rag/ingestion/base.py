"""The ingestion pipeline shape: chunk -> embed -> index.

Mirrors shopassist-service's `services/data_pipeline.py` at the interface
level — `Chunker` is the piece to replace with something that understands
real document formats (PDF, HTML); `ingest_document` is the reusable wiring
once a chunker exists.
"""

from __future__ import annotations

from typing import Any, Protocol

from air_rag.clients.model_gateway import ModelGatewayClient
from air_rag.retrieval.base import VectorStore


class Chunker(Protocol):
    def chunk(self, text: str) -> list[str]: ...


class FixedSizeChunker:
    """The naive baseline: fixed-size character windows, no overlap-aware
    sentence boundaries. Good enough to exercise the pipeline end to end;
    replace with something that respects document structure before real
    documents (docs/00-plan.md's Phase 1) go through this."""

    def __init__(self, *, size: int = 800) -> None:
        self._size = size

    def chunk(self, text: str) -> list[str]:
        stripped = text.strip()
        if not stripped:
            return []
        return [stripped[i : i + self._size] for i in range(0, len(stripped), self._size)]


async def ingest_document(
    *,
    source_id: str,
    text: str,
    metadata: dict[str, Any],
    chunker: Chunker,
    model_gateway: ModelGatewayClient,
    store: VectorStore,
) -> int:
    """Chunk, embed and index one document. Returns the chunk count actually
    stored — `0` for an empty document is a normal outcome, not an error."""
    chunks = chunker.chunk(text)
    if not chunks:
        return 0
    result = await model_gateway.embed(chunks)
    for index, (chunk_text, embedding) in enumerate(zip(chunks, result.embeddings, strict=False)):
        await store.upsert(
            f"{source_id}#{index}",
            embedding,
            chunk_text,
            {
                **metadata,
                "source_id": source_id,
                # Which embedding space this vector lives in: a model change is a re-index.
                "embedding_model": result.embedding_model,
            },
        )
    return min(len(chunks), len(result.embeddings))
