"""Operational surfaces: liveness, readiness, capabilities, metrics."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Response
from starlette.status import HTTP_503_SERVICE_UNAVAILABLE

from air_rag_service import __version__
from air_rag_service.api.deps import AppState, get_app_state, require_principal
from air_rag_service.api.errors import InsufficientScopeError
from air_rag_service.constants import SCOPE_ADMIN_READ
from air_rag_service.observability import metrics
from air_rag_service.schemas.common import (
    CapabilitiesResponse,
    HealthResponse,
    Principal,
    ReadyResponse,
)

__all__ = ["EMPTY_INDEX_WARNING", "build_metrics_router", "router"]

EMPTY_INDEX_WARNING = (
    "index is empty: every retrieval returns no citations (was the service started "
    "without ingestion.seed_dir?)"
)

router = APIRouter(tags=["system"])


@router.get(
    "/health",
    response_model=HealthResponse,
    summary="Liveness",
    description="Process is up. Consults nothing.",
)
async def health(state: Annotated[AppState, Depends(get_app_state)]) -> HealthResponse:
    return HealthResponse(service=state.settings.obs.service_name, version=__version__)


@router.get(
    "/ready",
    response_model=ReadyResponse,
    summary="Readiness",
    description=(
        "Whether this replica can serve a retrieval call. Gates on air-model-gateway "
        "serving this index's embedding model, the one dependency without which a "
        "query can't be embedded."
    ),
    responses={HTTP_503_SERVICE_UNAVAILABLE: {"model": ReadyResponse}},
)
async def ready(
    response: Response,
    state: Annotated[AppState, Depends(get_app_state)],
) -> ReadyResponse:
    gateway = await state.model_gateway.probe()
    ready_now = bool(gateway.reachable)
    if not ready_now:
        response.status_code = HTTP_503_SERVICE_UNAVAILABLE
    document_count = len(state.store)
    warnings = [EMPTY_INDEX_WARNING] if document_count == 0 else []
    return ReadyResponse(
        ready=ready_now,
        service=state.settings.obs.service_name,
        version=__version__,
        checked_at=datetime.now(UTC),
        dependencies=[gateway],
        document_count=document_count,
        warnings=warnings,
    )


@router.get(
    "/capabilities",
    response_model=CapabilitiesResponse,
    summary="What this deployment can do",
)
async def capabilities(
    state: Annotated[AppState, Depends(get_app_state)],
    principal: Annotated[Principal, Depends(require_principal)],
) -> CapabilitiesResponse:
    if not principal.has_scope(SCOPE_ADMIN_READ):
        raise InsufficientScopeError(f"This key lacks the '{SCOPE_ADMIN_READ}' scope.")
    settings = state.settings
    return CapabilitiesResponse(
        service=settings.obs.service_name,
        version=__version__,
        env=settings.app.env,
        vector_store_backend="memory",
        document_count=len(state.store),
        tenant=principal.tenant,
    )


def build_metrics_router(path: str) -> APIRouter:
    metrics_router = APIRouter(tags=["system"])

    @metrics_router.get(
        path, summary="Prometheus metrics", include_in_schema=False, response_class=Response
    )
    async def prometheus() -> Response:
        return Response(content=metrics.render(), media_type=metrics.CONTENT_TYPE)

    return metrics_router
