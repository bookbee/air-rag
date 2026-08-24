"""Shared fixtures.

Every test builds its app through `create_app()` with explicit settings.
`LLM_BASE_URL` is deliberately not localhost, so a probe escaping its stub
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

from air_rag.config import Settings
from air_rag.constants import SCOPE_ADMIN_READ, SCOPE_RETRIEVAL_READ, DownstreamService
from air_rag.main import create_app
from air_rag.schemas.common import DependencyStatus

TEST_SALT = "test-salt"
LLM_BASE_URL = "http://air-llm.invalid:8083"
DEV_KEY = "airr_test_key"
NO_SCOPE_KEY = "airr_test_noscope"


def digest(raw: str, salt: str = TEST_SALT) -> str:
    return hashlib.sha256((salt + raw).encode("utf-8")).hexdigest()


def key_records() -> list[dict[str, Any]]:
    return [
        {
            "id": "dev",
            "name": "Dev test key",
            "key_hash": digest(DEV_KEY),
            "scopes": [SCOPE_RETRIEVAL_READ, SCOPE_ADMIN_READ],
        },
        {
            "id": "noscope",
            "name": "Scopeless test key",
            "key_hash": digest(NO_SCOPE_KEY),
            "scopes": [],
        },
    ]


@pytest.fixture
def settings() -> Settings:
    return Settings.model_validate(
        {
            "app": {"env": "test", "port": 8087},
            "llm": {"base_url": LLM_BASE_URL},
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
def reachable_llm(app: FastAPI) -> Iterator[None]:
    from air_rag.api.deps import STATE_ATTR

    state = getattr(app.state, STATE_ATTR)

    async def healthy() -> DependencyStatus:
        return DependencyStatus(
            service=DownstreamService.LLM, configured=True, reachable=True, latency_ms=1.0
        )

    original = state.llm.probe
    state.llm.probe = healthy  # type: ignore[method-assign]
    try:
        yield
    finally:
        state.llm.probe = original  # type: ignore[method-assign]


@pytest.fixture
def unreachable_llm() -> Iterator[None]:
    with respx.mock:
        respx.get(f"{LLM_BASE_URL}/v1/ready").mock(
            side_effect=httpx.ConnectError("connection refused")
        )
        yield


def auth(key: str) -> dict[str, str]:
    return {"X-API-Key": key}
