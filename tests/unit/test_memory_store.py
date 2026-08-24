"""The in-process vector store: brute-force cosine similarity."""

from __future__ import annotations

from air_rag.retrieval.memory import InMemoryVectorStore


async def test_search_ranks_the_closer_vector_first() -> None:
    store = InMemoryVectorStore()
    await store.upsert("a", [1.0, 0.0], "identical direction", {})
    await store.upsert("b", [0.0, 1.0], "orthogonal", {})

    hits = await store.search([1.0, 0.0], top_k=2)

    assert [hit.source_id for hit in hits] == ["a", "b"]
    assert hits[0].score > hits[1].score


async def test_search_on_an_empty_store_returns_nothing() -> None:
    store = InMemoryVectorStore()

    assert await store.search([1.0, 0.0], top_k=5) == []


async def test_len_reflects_upserted_documents() -> None:
    store = InMemoryVectorStore()
    assert len(store) == 0

    await store.upsert("a", [1.0, 0.0], "text", {})

    assert len(store) == 1


async def test_a_zero_vector_scores_zero_rather_than_dividing_by_zero() -> None:
    store = InMemoryVectorStore()
    await store.upsert("a", [0.0, 0.0], "zero vector", {})

    hits = await store.search([1.0, 0.0], top_k=1)

    assert hits[0].score == 0.0
