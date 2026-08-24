"""`POST /v1/retrieve` — embed + search, end to end against the in-memory
store and a mocked air-llm."""

from __future__ import annotations

import httpx
import respx

from tests.conftest import DEV_KEY, LLM_BASE_URL, NO_SCOPE_KEY, auth


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
    respx.post(f"{LLM_BASE_URL}/v1/inference").mock(
        return_value=httpx.Response(200, json={"embeddings": [[0.1, 0.2, 0.3]]})
    )

    response = await client.post(
        "/v1/retrieve", json={"query": "return policy"}, headers=auth(DEV_KEY)
    )

    assert response.status_code == httpx.codes.OK
    body = response.json()
    assert body["query"] == "return policy"
    assert body["citations"] == []


@respx.mock
async def test_retrieve_degrades_cleanly_when_air_llm_is_unreachable(
    client: httpx.AsyncClient,
) -> None:
    respx.post(f"{LLM_BASE_URL}/v1/inference").mock(side_effect=httpx.ConnectError("refused"))

    response = await client.post(
        "/v1/retrieve", json={"query": "return policy"}, headers=auth(DEV_KEY)
    )

    assert response.status_code == httpx.codes.SERVICE_UNAVAILABLE
    assert response.headers["content-type"].startswith("application/problem+json")
