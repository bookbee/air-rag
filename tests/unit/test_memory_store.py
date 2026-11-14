"""The in-process vector store: brute-force cosine similarity."""

from __future__ import annotations

from air_rag_service.retrieval.memory import InMemoryVectorStore


async def test_search_ranks_the_closer_vector_first() -> None:
    store = InMemoryVectorStore()
    await store.upsert("a", [1.0, 0.0], "identical direction", {})
    await store.upsert("b", [0.0, 1.0], "orthogonal", {})

    hits = await store.search([1.0, 0.0], top_k=2, where={})

    assert [hit.source_id for hit in hits] == ["a", "b"]
    assert hits[0].score > hits[1].score


async def test_search_on_an_empty_store_returns_nothing() -> None:
    store = InMemoryVectorStore()

    assert await store.search([1.0, 0.0], top_k=5, where={}) == []


async def test_len_reflects_upserted_documents() -> None:
    store = InMemoryVectorStore()
    assert len(store) == 0

    await store.upsert("a", [1.0, 0.0], "text", {})

    assert len(store) == 1


async def test_a_zero_vector_scores_zero_rather_than_dividing_by_zero() -> None:
    store = InMemoryVectorStore()
    await store.upsert("a", [0.0, 0.0], "zero vector", {})

    hits = await store.search([1.0, 0.0], top_k=1, where={})

    assert hits[0].score == 0.0


async def test_where_filters_before_ranking_so_hidden_chunks_take_no_slot() -> None:
    store = InMemoryVectorStore()
    await store.upsert("theirs", [1.0, 0.0], "closest, other tenant", {"tenant": "acme"})
    await store.upsert("ours", [0.6, 0.8], "further, our tenant", {"tenant": "brightcart"})

    hits = await store.search([1.0, 0.0], top_k=1, where={"tenant": "brightcart"})

    assert [h.source_id for h in hits] == ["ours"]


async def test_where_requires_every_pair_to_match() -> None:
    store = InMemoryVectorStore()
    await store.upsert("old", [1.0, 0.0], "x", {"tenant": "t", "status": "archived"})
    await store.upsert("new", [1.0, 0.0], "x", {"tenant": "t", "status": "active"})
    await store.upsert("bare", [1.0, 0.0], "x", {})

    hits = await store.search([1.0, 0.0], top_k=5, where={"tenant": "t", "status": "active"})

    assert [h.source_id for h in hits] == ["new"]
