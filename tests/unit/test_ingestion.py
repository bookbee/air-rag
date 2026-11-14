"""The chunk -> embed -> index pipeline, against the in-memory store and a
fake gateway client (no network — this exercises the wiring, not
air-model-gateway)."""

from __future__ import annotations

from air_rag.clients.model_gateway import EmbeddingResult
from air_rag.constants import DocumentStatus
from air_rag.ingestion.base import FixedSizeChunker, ingest_document
from air_rag.retrieval.memory import InMemoryVectorStore


class _FakeGatewayClient:
    async def embed(self, texts: list[str]) -> EmbeddingResult:
        return EmbeddingResult(
            embeddings=[[float(len(t)), 0.0] for t in texts],
            embedding_model="local-768-v1",
            dimensions=2,
        )


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
        tenant="brightcart",
        text="a short policy document",
        metadata={"kind": "policy"},
        chunker=chunker,
        model_gateway=_FakeGatewayClient(),  # type: ignore[arg-type]
        store=store,
    )

    assert count == 1
    assert len(store) == 1
    [hit] = await store.search([23.0, 0.0], top_k=1, where={})
    assert hit.source_id == "doc-1#0"
    assert hit.metadata == {
        "kind": "policy",
        "source_id": "doc-1",
        "embedding_model": "local-768-v1",
        "tenant": "brightcart",
        "status": "active",
    }


async def test_ingest_document_on_empty_text_stores_nothing() -> None:
    store = InMemoryVectorStore()

    count = await ingest_document(
        source_id="doc-1",
        tenant="brightcart",
        text="   ",
        metadata={},
        chunker=FixedSizeChunker(),
        model_gateway=_FakeGatewayClient(),  # type: ignore[arg-type]
        store=store,
    )

    assert count == 0
    assert len(store) == 0


async def test_caller_metadata_cannot_override_tenant_or_status() -> None:
    store = InMemoryVectorStore()

    await ingest_document(
        source_id="doc-1",
        tenant="brightcart",
        status=DocumentStatus.ARCHIVED,
        text="text",
        metadata={"tenant": "acme", "status": "active"},
        chunker=FixedSizeChunker(),
        model_gateway=_FakeGatewayClient(),  # type: ignore[arg-type]
        store=store,
    )

    [hit] = await store.search([4.0, 0.0], top_k=1, where={})
    assert (hit.metadata["tenant"], hit.metadata["status"]) == ("brightcart", "archived")
