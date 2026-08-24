"""The chunk -> embed -> index pipeline, against the in-memory store and a
fake LLM client (no network — this exercises the wiring, not air-llm)."""

from __future__ import annotations

from air_rag.ingestion.base import FixedSizeChunker, ingest_document
from air_rag.retrieval.memory import InMemoryVectorStore


class _FakeLlmClient:
    async def embed(self, texts: list[str], *, model: str = "embeddings") -> _FakeResult:
        return _FakeResult(embeddings=[[float(len(t)), 0.0] for t in texts])


class _FakeResult:
    def __init__(self, embeddings: list[list[float]]) -> None:
        self.embeddings = embeddings


def test_fixed_size_chunker_splits_on_the_configured_size() -> None:
    chunker = FixedSizeChunker(size=5)

    assert chunker.chunk("abcdefghij") == ["abcde", "fghij"]


def test_fixed_size_chunker_returns_nothing_for_blank_text() -> None:
    assert FixedSizeChunker().chunk("   ") == []


async def test_ingest_document_chunks_embeds_and_upserts() -> None:
    store = InMemoryVectorStore()
    chunker = FixedSizeChunker(size=100)

    count = await ingest_document(
        source_id="doc-1",
        text="a short policy document",
        metadata={"kind": "policy"},
        chunker=chunker,
        llm=_FakeLlmClient(),  # type: ignore[arg-type]
        store=store,
    )

    assert count == 1
    assert len(store) == 1


async def test_ingest_document_on_empty_text_stores_nothing() -> None:
    store = InMemoryVectorStore()

    count = await ingest_document(
        source_id="doc-1",
        text="   ",
        metadata={},
        chunker=FixedSizeChunker(),
        llm=_FakeLlmClient(),  # type: ignore[arg-type]
        store=store,
    )

    assert count == 0
    assert len(store) == 0
