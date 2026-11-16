"""`POST /v1/retrieve` — the call air-platform's GATHER step makes
(air-platform/docs/01-hld.md §4: "air-rag-service — retrieval + citations").

Read-only: this route embeds a query and searches; nothing here writes to the
index. Ingestion is a separate, not-yet-built path (`ingestion/base.py`).
"""

from __future__ import annotations

import time
from typing import Annotated

from fastapi import APIRouter, Depends

from air_rag_service.api.deps import AppState, get_app_state, require_scope
from air_rag_service.api.errors import DependencyUnavailableError
from air_rag_service.clients.model_gateway import ModelGatewayError
from air_rag_service.constants import META_STATUS, META_TENANT, SCOPE_RETRIEVAL_READ, DocumentStatus
from air_rag_service.observability import metrics
from air_rag_service.observability.logging import get_logger
from air_rag_service.schemas.common import Principal
from air_rag_service.schemas.retrieval import Citation, RetrievalRequest, RetrievalResponse

logger = get_logger(__name__)

router = APIRouter(tags=["retrieval"])


@router.post(
    "/retrieve",
    response_model=RetrievalResponse,
    summary="Embed a query and search the index",
)
async def retrieve(
    request: RetrievalRequest,
    state: Annotated[AppState, Depends(get_app_state)],
    principal: Annotated[Principal, require_scope(SCOPE_RETRIEVAL_READ)],
) -> RetrievalResponse:
    started = time.perf_counter()
    try:
        embedded = await state.model_gateway.embed([request.query])
    except ModelGatewayError as exc:
        metrics.record_retrieval(outcome="error", duration_s=time.perf_counter() - started)
        logger.warning("retrieval.embed_failed", error=str(exc))
        raise DependencyUnavailableError(
            "Could not embed the query through air-model-gateway."
        ) from exc

    if not embedded.embeddings:
        metrics.record_retrieval(outcome="empty", duration_s=time.perf_counter() - started)
        return RetrievalResponse(query=request.query, citations=[])

    # The tenant comes from the key, never the body; archived documents stay
    # indexed but are never returned.
    hits = await state.store.search(
        embedded.embeddings[0],
        top_k=request.top_k,
        where={META_TENANT: principal.tenant, META_STATUS: DocumentStatus.ACTIVE},
    )
    # Below the floor a chunk is noise, not a weaker match (SIT RG-004).
    floor = state.settings.retrieval.min_score
    hits = [hit for hit in hits if hit.score >= floor]
    metrics.record_retrieval(
        outcome="hit" if hits else "empty", duration_s=time.perf_counter() - started
    )
    return RetrievalResponse(
        query=request.query,
        citations=[
            Citation(
                source_id=hit.source_id,
                snippet=hit.text,
                score=hit.score,
                metadata=hit.metadata,
            )
            for hit in hits
        ],
    )
