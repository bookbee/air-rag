"""The air-model-gateway client: the `/v1/embed` contract and the readiness probe."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
import respx
import structlog

from air_rag_service.clients.model_gateway import ModelGatewayClient, ModelGatewayError
from air_rag_service.config import Settings
from tests.conftest import EMBED_DIMS, EMBED_MODEL, embed_response, vector

_GW = "http://gateway:8083"


def _client(**gateway: object) -> ModelGatewayClient:
    return ModelGatewayClient(
        Settings.model_validate(
            {
                "app": {"env": "test"},
                "model_gateway": {"base_url": _GW, "api_key": "air-rag-test", **gateway},
            }
        )
    )


def _capabilities(**entry: Any) -> dict[str, Any]:
    published = {
        "allowed": True,
        "available": True,
        "provider": "ollama",
        "model": "nomic-embed-text",
        "dimensions": EMBED_DIMS,
        **entry,
    }
    return {"embed": {EMBED_MODEL: published}}


def _ready(**providers: bool) -> dict[str, Any]:
    return {"status": "ready", "providers": {"ollama": True, **providers}}


# ── embed ─────────────────────────────────────────────────────────────────────


@respx.mock
async def test_embed_posts_the_published_model_and_no_task_field() -> None:
    route = respx.post(f"{_GW}/v1/embed").mock(
        return_value=httpx.Response(200, json=embed_response([vector(0.1), vector(0.2)]))
    )
    client = _client()

    result = await client.embed(["hello", "world"])

    sent = json.loads(route.calls.last.request.content)
    assert sent == {"model": EMBED_MODEL, "input": ["hello", "world"]}
    assert route.calls.last.request.headers["X-API-Key"] == "air-rag-test"
    assert [v[0] for v in result.embeddings] == [0.1, 0.2]
    assert result.embedding_model == EMBED_MODEL
    await client.aclose()


@respx.mock
async def test_embed_forwards_the_request_id() -> None:
    route = respx.post(f"{_GW}/v1/embed").mock(
        return_value=httpx.Response(200, json=embed_response([vector(0.1)]))
    )
    client = _client()
    structlog.contextvars.bind_contextvars(request_id="req_abc")
    try:
        await client.embed(["hello"])
    finally:
        structlog.contextvars.clear_contextvars()

    assert route.calls.last.request.headers["X-Request-ID"] == "req_abc"
    await client.aclose()


@respx.mock
async def test_embed_splits_large_inputs_into_batches_and_keeps_order() -> None:
    def answer(request: httpx.Request) -> httpx.Response:
        texts = json.loads(request.content)["input"]
        return httpx.Response(200, json=embed_response([vector(float(t)) for t in texts]))

    route = respx.post(f"{_GW}/v1/embed").mock(side_effect=answer)
    client = _client(embed_batch_size=2)

    result = await client.embed(["1", "2", "3", "4", "5"])

    assert route.call_count == 3
    assert [v[0] for v in result.embeddings] == [1.0, 2.0, 3.0, 4.0, 5.0]
    await client.aclose()


async def test_embed_of_nothing_makes_no_call() -> None:
    client = _client()

    result = await client.embed([])

    assert result.embeddings == []
    await client.aclose()


@respx.mock
async def test_embed_rejects_vectors_from_another_embedding_space() -> None:
    respx.post(f"{_GW}/v1/embed").mock(
        return_value=httpx.Response(
            200, json={**embed_response([[0.1] * 1536]), "embedding_model": "cloud-1536-v1"}
        )
    )
    client = _client()

    with pytest.raises(ModelGatewayError, match="cloud-1536-v1"):
        await client.embed(["hello"])
    await client.aclose()


@respx.mock
async def test_embed_surfaces_the_gateways_problem_code_not_its_body() -> None:
    respx.post(f"{_GW}/v1/embed").mock(
        return_value=httpx.Response(
            403, json={"code": "policy_denied", "detail": "internal detail", "status": 403}
        )
    )
    client = _client()

    with pytest.raises(ModelGatewayError) as raised:
        await client.embed(["hello"])

    assert "policy_denied" in str(raised.value)
    assert "internal detail" not in str(raised.value)
    await client.aclose()


@respx.mock
async def test_embed_raises_on_a_connection_failure() -> None:
    respx.post(f"{_GW}/v1/embed").mock(side_effect=httpx.ConnectError("boom"))
    client = _client()

    with pytest.raises(ModelGatewayError):
        await client.embed(["hello"])
    await client.aclose()


# ── probe ─────────────────────────────────────────────────────────────────────


@respx.mock
async def test_probe_reports_ready_when_the_embedding_model_can_serve() -> None:
    respx.get(f"{_GW}/v1/capabilities").mock(return_value=httpx.Response(200, json=_capabilities()))
    respx.get(f"{_GW}/v1/ready").mock(return_value=httpx.Response(200, json=_ready()))
    client = _client()

    status = await client.probe()

    assert status.reachable is True
    assert status.service == "air-model-gateway"
    await client.aclose()


@respx.mock
async def test_probe_is_not_ready_when_only_other_providers_answer() -> None:
    """The gateway's own /v1/ready is green while any provider answers."""
    respx.get(f"{_GW}/v1/capabilities").mock(return_value=httpx.Response(200, json=_capabilities()))
    respx.get(f"{_GW}/v1/ready").mock(
        return_value=httpx.Response(200, json=_ready(ollama=False, anthropic=True))
    )
    client = _client()

    status = await client.probe()

    assert status.reachable is False
    assert "ollama" in (status.detail or "")
    await client.aclose()


@pytest.mark.parametrize(
    ("entry", "reason"),
    [
        ({"allowed": False}, "not granted"),
        ({"available": False}, "cannot serve"),
        ({"dimensions": 1536}, "1536"),
    ],
)
@respx.mock
async def test_probe_explains_why_the_embedding_model_cannot_serve(
    entry: dict[str, Any], reason: str
) -> None:
    respx.get(f"{_GW}/v1/capabilities").mock(
        return_value=httpx.Response(200, json=_capabilities(**entry))
    )
    client = _client()

    status = await client.probe()

    assert status.reachable is False
    assert reason in (status.detail or "")
    await client.aclose()


@respx.mock
async def test_probe_reports_a_rejected_token() -> None:
    respx.get(f"{_GW}/v1/capabilities").mock(
        return_value=httpx.Response(401, json={"code": "unauthorized"})
    )
    client = _client()

    status = await client.probe()

    assert status.reachable is False
    assert "unauthorized" in (status.detail or "")
    await client.aclose()


@respx.mock
async def test_probe_never_raises_on_a_connection_failure() -> None:
    respx.get(f"{_GW}/v1/capabilities").mock(side_effect=httpx.ConnectError("boom"))
    client = _client()

    status = await client.probe()

    assert status.reachable is False
    assert "http://" not in (status.detail or "")
    await client.aclose()
