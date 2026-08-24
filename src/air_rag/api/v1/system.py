"""Operational surfaces: liveness, readiness, capabilities, metrics."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Response
from starlette.status import HTTP_503_SERVICE_UNAVAILABLE

from air_rag import __version__
from air_rag.api.deps import AppState, get_app_state, require_principal
from air_rag.api.errors import InsufficientScopeError
from air_rag.constants import SCOPE_ADMIN_READ
from air_rag.observability import metrics
from air_rag.schemas.common import CapabilitiesResponse, HealthResponse, Principal, ReadyResponse

__all__ = ["build_metrics_router", "router"]

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
        "Whether this replica can serve a retrieval call. Gates on air-llm, the one "
        "dependency without which a query can't be embedded."
    ),
    responses={HTTP_503_SERVICE_UNAVAILABLE: {"model": ReadyResponse}},
)
async def ready(
    response: Response,
    state: Annotated[AppState, Depends(get_app_state)],
) -> ReadyResponse:
    llm = await state.llm.probe()
    ready_now = bool(llm.reachable)
    if not ready_now:
        response.status_code = HTTP_503_SERVICE_UNAVAILABLE
    return ReadyResponse(
        ready=ready_now,
        service=state.settings.obs.service_name,
        version=__version__,
        checked_at=datetime.now(UTC),
        dependencies=[llm],
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
    )


def build_metrics_router(path: str) -> APIRouter:
    metrics_router = APIRouter(tags=["system"])

    @metrics_router.get(
        path, summary="Prometheus metrics", include_in_schema=False, response_class=Response
    )
    async def prometheus() -> Response:
        return Response(content=metrics.render(), media_type=metrics.CONTENT_TYPE)

    return metrics_router
