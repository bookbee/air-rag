"""The air-llm probe and embed call."""

from __future__ import annotations

import httpx
import respx

from air_rag.clients.llm import LlmClient
from air_rag.config import Settings


def _client(**llm: object) -> LlmClient:
    return LlmClient(
        Settings.model_validate(
            {"app": {"env": "test"}, "llm": {"base_url": "http://gateway:8083", **llm}}
        )
    )


@respx.mock
async def test_probe_reports_a_healthy_gateway() -> None:
    respx.get("http://gateway:8083/v1/ready").mock(return_value=httpx.Response(200))
    client = _client()

    status = await client.probe()

    assert status.reachable is True
    await client.aclose()


@respx.mock
async def test_probe_never_raises_on_a_connection_failure() -> None:
    respx.get("http://gateway:8083/v1/ready").mock(side_effect=httpx.ConnectError("boom"))
    client = _client()

    status = await client.probe()

    assert status.reachable is False
    assert "http://" not in (status.detail or "")
    await client.aclose()


@respx.mock
async def test_embed_returns_the_vectors() -> None:
    respx.post("http://gateway:8083/v1/inference").mock(
        return_value=httpx.Response(200, json={"embeddings": [[0.1, 0.2]]})
    )
    client = _client()

    result = await client.embed(["hello"])

    assert result.embeddings == [[0.1, 0.2]]
    await client.aclose()


@respx.mock
async def test_embed_raises_on_a_connection_failure() -> None:
    respx.post("http://gateway:8083/v1/inference").mock(side_effect=httpx.ConnectError("boom"))
    client = _client()

    try:
        await client.embed(["hello"])
        raised = False
    except httpx.HTTPError:
        raised = True

    assert raised
    await client.aclose()
