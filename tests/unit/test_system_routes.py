"""The three operational surfaces."""

from __future__ import annotations

import httpx
import pytest
from fastapi import FastAPI

from tests.conftest import DEV_KEY, NO_SCOPE_KEY, auth


async def test_health_needs_no_auth_and_no_dependencies(client: httpx.AsyncClient) -> None:
    response = await client.get("/v1/health")

    assert response.status_code == httpx.codes.OK
    body = response.json()
    assert body["status"] == "ok"
    assert body["service"] == "air-rag"


@pytest.mark.usefixtures("unreachable_gateway")
async def test_ready_is_503_when_the_gateway_is_unreachable(client: httpx.AsyncClient) -> None:
    response = await client.get("/v1/ready")

    assert response.status_code == httpx.codes.SERVICE_UNAVAILABLE
    assert response.json()["ready"] is False


@pytest.mark.usefixtures("reachable_gateway")
async def test_ready_is_200_when_the_gateway_is_reachable(client: httpx.AsyncClient) -> None:
    response = await client.get("/v1/ready")

    assert response.status_code == httpx.codes.OK
    assert response.json()["ready"] is True


async def test_capabilities_requires_a_key(client: httpx.AsyncClient) -> None:
    response = await client.get("/v1/capabilities")

    assert response.status_code == httpx.codes.UNAUTHORIZED


async def test_capabilities_requires_admin_read(client: httpx.AsyncClient) -> None:
    response = await client.get("/v1/capabilities", headers=auth(NO_SCOPE_KEY))

    assert response.status_code == httpx.codes.FORBIDDEN


async def test_capabilities_reports_the_memory_backend_and_doc_count(
    client: httpx.AsyncClient,
) -> None:
    response = await client.get("/v1/capabilities", headers=auth(DEV_KEY))

    assert response.status_code == httpx.codes.OK
    body = response.json()
    assert body["vector_store_backend"] == "memory"
    assert body["document_count"] == 0


async def test_metrics_exposes_the_prometheus_payload(client: httpx.AsyncClient) -> None:
    response = await client.get("/metrics")

    assert response.status_code == httpx.codes.OK
    assert "air_rag_requests_total" in response.text


async def test_metrics_is_absent_from_the_openapi_document(app: FastAPI) -> None:
    assert "/metrics" not in app.openapi()["paths"]
