"""`POST /v1/retrieve` — embed + search, end to end against the in-memory
store and a mocked air-model-gateway."""

from __future__ import annotations

import httpx
import respx
from fastapi import FastAPI

from air_rag.api.deps import STATE_ATTR
from tests.conftest import (
    DEV_KEY,
    GATEWAY_BASE_URL,
    NO_SCOPE_KEY,
    auth,
    embed_response,
    vector,
)


async def test_retrieve_requires_a_key(client: httpx.AsyncClient) -> None:
    response = await client.post("/v1/retrieve", json={"query": "return policy"})

    assert response.status_code == httpx.codes.UNAUTHORIZED


async def test_retrieve_requires_retrieval_read_scope(client: httpx.AsyncClient) -> None:
    response = await client.post(
        "/v1/retrieve", json={"query": "return policy"}, headers=auth(NO_SCOPE_KEY)
    )

    assert response.status_code == httpx.codes.FORBIDDEN


@respx.mock
async def test_retrieve_against_an_empty_index_returns_no_citations(
    client: httpx.AsyncClient,
) -> None:
    """Nothing ingested yet in this Phase-0 build — an empty result is the
    honest answer, not an error."""
    respx.post(f"{GATEWAY_BASE_URL}/v1/embed").mock(
        return_value=httpx.Response(200, json=embed_response([vector(0.1, 0.2, 0.3)]))
    )

    response = await client.post(
        "/v1/retrieve", json={"query": "return policy"}, headers=auth(DEV_KEY)
    )

    assert response.status_code == httpx.codes.OK
    body = response.json()
    assert body["query"] == "return policy"
    assert body["citations"] == []


@respx.mock
async def test_retrieve_degrades_cleanly_when_the_gateway_is_unreachable(
    client: httpx.AsyncClient,
) -> None:
    respx.post(f"{GATEWAY_BASE_URL}/v1/embed").mock(side_effect=httpx.ConnectError("refused"))

    response = await client.post(
        "/v1/retrieve", json={"query": "return policy"}, headers=auth(DEV_KEY)
    )

    assert response.status_code == httpx.codes.SERVICE_UNAVAILABLE
    assert response.headers["content-type"].startswith("application/problem+json")


@respx.mock
async def test_retrieve_returns_the_nearest_chunk_first(
    app: FastAPI, client: httpx.AsyncClient
) -> None:
    store = getattr(app.state, STATE_ATTR).store
    await store.upsert("returns#0", vector(1.0, 0.0), "30-day returns", {"kind": "policy"})
    await store.upsert("shipping#0", vector(0.0, 1.0), "Ships in 2 days", {"kind": "policy"})
    respx.post(f"{GATEWAY_BASE_URL}/v1/embed").mock(
        return_value=httpx.Response(200, json=embed_response([vector(0.9, 0.1)]))
    )

    response = await client.post(
        "/v1/retrieve", json={"query": "how do returns work", "top_k": 1}, headers=auth(DEV_KEY)
    )

    assert response.status_code == httpx.codes.OK
    citations = response.json()["citations"]
    assert [c["source_id"] for c in citations] == ["returns#0"]


@respx.mock
async def test_retrieve_is_503_when_the_gateway_answers_with_another_embedding_space(
    client: httpx.AsyncClient,
) -> None:
    """Vectors from another model would silently corrupt the ranking."""
    respx.post(f"{GATEWAY_BASE_URL}/v1/embed").mock(
        return_value=httpx.Response(200, json=embed_response([[0.1] * 1536]))
    )

    response = await client.post(
        "/v1/retrieve", json={"query": "return policy"}, headers=auth(DEV_KEY)
    )

    assert response.status_code == httpx.codes.SERVICE_UNAVAILABLE
