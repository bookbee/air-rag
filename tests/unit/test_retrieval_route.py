"""`POST /v1/retrieve` — embed + search, end to end against the in-memory
store and a mocked air-model-gateway."""

from __future__ import annotations

from typing import Any

import httpx
import respx
from fastapi import FastAPI

from air_rag_service.api.deps import STATE_ATTR
from tests.conftest import (
    DEV_KEY,
    GATEWAY_BASE_URL,
    NO_SCOPE_KEY,
    OTHER_TENANT_KEY,
    TENANT,
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


def _meta(tenant: str = TENANT, status: str = "active") -> dict[str, Any]:
    return {"kind": "policy", "tenant": tenant, "status": status}


def _mock_embed(*head: float) -> None:
    respx.post(f"{GATEWAY_BASE_URL}/v1/embed").mock(
        return_value=httpx.Response(200, json=embed_response([vector(*head)]))
    )


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
    await store.upsert("returns#0", vector(1.0, 0.0), "30-day returns", _meta())
    await store.upsert("shipping#0", vector(0.0, 1.0), "Ships in 2 days", _meta())
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


@respx.mock
async def test_retrieve_searches_only_the_keys_tenant(
    app: FastAPI, client: httpx.AsyncClient
) -> None:
    store = getattr(app.state, STATE_ATTR).store
    await store.upsert("ours#0", vector(0.6, 0.8), "our policy", _meta())
    await store.upsert("theirs#0", vector(1.0, 0.0), "their policy", _meta(tenant="acme"))
    _mock_embed(1.0, 0.0)

    ours = await client.post("/v1/retrieve", json={"query": "q"}, headers=auth(DEV_KEY))
    theirs = await client.post("/v1/retrieve", json={"query": "q"}, headers=auth(OTHER_TENANT_KEY))

    assert [c["source_id"] for c in ours.json()["citations"]] == ["ours#0"]
    assert [c["source_id"] for c in theirs.json()["citations"]] == ["theirs#0"]


@respx.mock
async def test_retrieve_never_returns_archived_documents(
    app: FastAPI, client: httpx.AsyncClient
) -> None:
    store = getattr(app.state, STATE_ATTR).store
    await store.upsert("old#0", vector(1.0, 0.0), "15-day returns", _meta(status="archived"))
    await store.upsert("new#0", vector(0.6, 0.8), "30-day returns", _meta())
    _mock_embed(1.0, 0.0)

    response = await client.post("/v1/retrieve", json={"query": "q"}, headers=auth(DEV_KEY))

    assert [c["source_id"] for c in response.json()["citations"]] == ["new#0"]


async def test_retrieve_rejects_unknown_fields_such_as_a_body_tenant(
    client: httpx.AsyncClient,
) -> None:
    """The tenant comes from the key; a body `tenant` must not look honoured."""
    response = await client.post(
        "/v1/retrieve", json={"query": "q", "tenant": "acme"}, headers=auth(DEV_KEY)
    )

    assert response.status_code == httpx.codes.UNPROCESSABLE_ENTITY
    assert response.json()["errors"][0]["field"] == "tenant"
