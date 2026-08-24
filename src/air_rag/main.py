"""The application factory.

Phase-0 skeleton: `create_app(settings)` never reaches for the environment on
its own. The one thing that gates readiness is air-llm; the service otherwise
boots and serves `/v1/health` regardless.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, Final

import orjson
from fastapi import FastAPI
from fastapi.responses import JSONResponse

from air_rag import __version__
from air_rag.api.deps import STATE_ATTR, AppState
from air_rag.api.errors import install_error_handlers
from air_rag.api.middleware import install_middleware
from air_rag.api.v1.router import api_router
from air_rag.api.v1.system import build_metrics_router
from air_rag.clients.llm import LlmClient
from air_rag.config import Settings, get_settings
from air_rag.observability.logging import configure_logging, get_logger
from air_rag.retrieval.memory import InMemoryVectorStore
from air_rag.security.api_keys import ApiKeyStore

__all__ = ["OrjsonResponse", "app", "create_app"]

_log = get_logger(__name__)

_TITLE: Final[str] = "AIR RAG"
_DESCRIPTION: Final[str] = """
Ingestion, indexing and retrieval for the AIR platform.

`POST /v1/retrieve` embeds a query against air-llm and searches the index,
returning citations air-platform's synthesis step grounds an answer in. This
service holds no mutating surface toward any business system — it only reads
and writes its own index.
""".strip()


class OrjsonResponse(JSONResponse):
    def render(self, content: Any) -> bytes:
        return orjson.dumps(content, option=orjson.OPT_NON_STR_KEYS)


def _build_lifespan(state: AppState) -> Any:
    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        _log.info(
            "startup.complete",
            version=__version__,
            env=state.settings.app.env,
            port=state.settings.app.port,
            api_keys=len(state.key_store),
            document_count=len(state.store),
        )
        try:
            yield
        finally:
            await state.llm.aclose()
            _log.info("shutdown.complete")

    return lifespan


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved = settings or get_settings()
    configure_logging(resolved)

    key_store = ApiKeyStore.load(resolved)

    state = AppState(
        settings=resolved,
        key_store=key_store,
        llm=LlmClient(resolved),
        store=InMemoryVectorStore(),
    )

    app = FastAPI(
        title=_TITLE,
        description=_DESCRIPTION,
        version=__version__,
        docs_url="/docs" if resolved.app.docs_enabled else None,
        redoc_url="/redoc" if resolved.app.docs_enabled else None,
        openapi_url="/openapi.json" if resolved.app.docs_enabled else None,
        default_response_class=OrjsonResponse,
        lifespan=_build_lifespan(state),
    )
    setattr(app.state, STATE_ATTR, state)

    install_middleware(app, resolved)
    install_error_handlers(app)
    app.include_router(api_router, prefix="/v1")
    if resolved.obs.metrics_enabled:
        app.include_router(build_metrics_router(resolved.obs.metrics_path))

    return app


app = create_app()
