"""Shared fixtures.

Every test builds its app through `create_app()` with explicit settings.
`GATEWAY_BASE_URL` is deliberately not localhost, so a probe escaping its stub
fails loudly rather than quietly reaching a real service.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import AsyncIterator, Iterator
from typing import Any

import httpx
import pytest
import respx
from fastapi import FastAPI

from air_rag_service.config import Settings
from air_rag_service.constants import SCOPE_ADMIN_READ, SCOPE_RETRIEVAL_READ, DownstreamService
from air_rag_service.main import create_app
from air_rag_service.schemas.common import DependencyStatus

TEST_SALT = "test-salt"
GATEWAY_BASE_URL = "http://air-model-gateway.invalid:8083"
GATEWAY_TOKEN = "air-rag-test"
EMBED_MODEL = "local-768-v1"
EMBED_DIMS = 768
DEV_KEY = "airr_test_key"
NO_SCOPE_KEY = "airr_test_noscope"
OTHER_TENANT_KEY = "airr_test_other_tenant"
TENANT = "brightcart"


def digest(raw: str, salt: str = TEST_SALT) -> str:
    return hashlib.sha256((salt + raw).encode("utf-8")).hexdigest()


def key_records() -> list[dict[str, Any]]:
    return [
        {
            "id": "dev",
            "name": "Dev test key",
            "key_hash": digest(DEV_KEY),
            "tenant": TENANT,
            "scopes": [SCOPE_RETRIEVAL_READ, SCOPE_ADMIN_READ],
        },
        {
            "id": "noscope",
            "name": "Scopeless test key",
            "key_hash": digest(NO_SCOPE_KEY),
            "tenant": TENANT,
            "scopes": [],
        },
        {
            "id": "other",
            "name": "Other-tenant test key",
            "key_hash": digest(OTHER_TENANT_KEY),
            "tenant": "acme",
            "scopes": [SCOPE_RETRIEVAL_READ],
        },
    ]


@pytest.fixture
def settings() -> Settings:
    return Settings.model_validate(
        {
            "app": {"env": "test", "port": 8087},
            "model_gateway": {"base_url": GATEWAY_BASE_URL, "api_key": GATEWAY_TOKEN},
            "security": {
                "hash_salt": TEST_SALT,
                "api_keys_inline": json.dumps(key_records()),
                "allow_unauthenticated": False,
            },
            "obs": {"log_format": "console", "metrics_enabled": True},
        }
    )


@pytest.fixture
def app(settings: Settings) -> FastAPI:
    return create_app(settings)


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
        yield http


@pytest.fixture
def reachable_gateway(app: FastAPI) -> Iterator[None]:
    from air_rag_service.api.deps import STATE_ATTR

    state = getattr(app.state, STATE_ATTR)

    async def healthy() -> DependencyStatus:
        return DependencyStatus(
            service=DownstreamService.MODEL_GATEWAY, configured=True, reachable=True, latency_ms=1.0
        )

    original = state.model_gateway.probe
    state.model_gateway.probe = healthy  # type: ignore[method-assign]
    try:
        yield
    finally:
        state.model_gateway.probe = original  # type: ignore[method-assign]


@pytest.fixture
def unreachable_gateway() -> Iterator[None]:
    with respx.mock:
        respx.get(f"{GATEWAY_BASE_URL}/v1/capabilities").mock(
            side_effect=httpx.ConnectError("connection refused")
        )
        yield


def embed_response(vectors: list[list[float]]) -> dict[str, Any]:
    """A `/v1/embed` body as the gateway sends it."""
    return {
        "id": "emb_test",
        "embedding_model": EMBED_MODEL,
        "dimensions": len(vectors[0]) if vectors else EMBED_DIMS,
        "model": "nomic-embed-text",
        "provider": "ollama",
        "embeddings": vectors,
        "usage": {"prompt_tokens": 1, "completion_tokens": 0, "total_tokens": 1},
        "cost_usd": 0.0,
    }


def vector(*head: float) -> list[float]:
    """A 768-d vector whose first components are `head`, the rest zero."""
    return [*head, *([0.0] * (EMBED_DIMS - len(head)))]


def auth(key: str) -> dict[str, str]:
    return {"X-API-Key": key}
