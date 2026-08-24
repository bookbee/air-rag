"""air-llm client — the model gateway ingestion and query-time embedding both
go through.

Reached over HTTP, not imported as a package — the precedent every AIR
service follows for air-llm. `probe()` mirrors
`air_platform.clients.llm.LlmClient`/`air_tools.clients.llm.LlmClient`,
hitting `/v1/ready` rather than `/v1/health` for the same reason: air-llm's
health route is dependency-free liveness, `/v1/ready` is gated on a provider
actually answering.

`embed()` is this repo's equivalent of air-tools' `chat()` — the one call
retrieval and ingestion both actually need. Wire-shaped after air-llm's
`POST /v1/inference` with `task="embeddings"`.
"""

from __future__ import annotations

import asyncio
import time
from typing import Final

import httpx
from pydantic import BaseModel, ConfigDict

from air_rag.config import Settings
from air_rag.constants import HEADER_API_KEY, DownstreamService
from air_rag.observability import metrics
from air_rag.observability.logging import get_logger
from air_rag.schemas.common import DependencyStatus

__all__ = ["EmbeddingResult", "LlmClient"]

logger = get_logger(__name__)

_READY_PATH: Final[str] = "/v1/ready"
_INFERENCE_PATH: Final[str] = "/v1/inference"


class EmbeddingResult(BaseModel):
    model_config = ConfigDict(extra="ignore")

    embeddings: list[list[float]] = []
    cost_usd: float = 0.0


class LlmClient:
    def __init__(self, settings: Settings) -> None:
        self._config = settings.llm
        self._client: httpx.AsyncClient | None = None
        self._lock = asyncio.Lock()
        self._closed = False

    async def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            async with self._lock:
                if self._client is None:
                    headers = {}
                    if self._config.api_key is not None:
                        headers[HEADER_API_KEY] = self._config.api_key.get_secret_value()
                    self._client = httpx.AsyncClient(
                        base_url=self._config.base_url,
                        timeout=self._config.timeout_s,
                        headers=headers,
                    )
        return self._client

    async def probe(self) -> DependencyStatus:
        client = await self._http()
        started = time.perf_counter()
        try:
            response = await client.get(_READY_PATH, timeout=self._config.health_timeout_s)
            elapsed_ms = (time.perf_counter() - started) * 1000
            ok = response.status_code == httpx.codes.OK
            metrics.record_downstream_call(
                service=DownstreamService.LLM, outcome="ok" if ok else "error"
            )
            return DependencyStatus(
                service=DownstreamService.LLM,
                configured=True,
                reachable=ok,
                latency_ms=round(elapsed_ms, 3),
                detail=None if ok else f"air-llm returned HTTP {response.status_code}",
            )
        except httpx.TimeoutException:
            metrics.record_downstream_call(service=DownstreamService.LLM, outcome="timeout")
            return DependencyStatus(
                service=DownstreamService.LLM,
                configured=True,
                reachable=False,
                latency_ms=round((time.perf_counter() - started) * 1000, 3),
                detail=f"no response within {self._config.health_timeout_s}s",
            )
        except httpx.HTTPError as exc:
            metrics.record_downstream_call(service=DownstreamService.LLM, outcome="unavailable")
            logger.warning("llm.probe_failed", error=type(exc).__name__)
            return DependencyStatus(
                service=DownstreamService.LLM,
                configured=True,
                reachable=False,
                latency_ms=round((time.perf_counter() - started) * 1000, 3),
                detail=f"connection failed ({type(exc).__name__})",
            )

    async def embed(self, texts: list[str], *, model: str = "embeddings") -> EmbeddingResult:
        """Raises `httpx.HTTPError` on failure — the caller (retrieval or
        ingestion) decides its own fallback; unlike `probe()`, this does not
        swallow the error."""
        client = await self._http()
        body = {"task": "embeddings", "model": model, "input": texts}
        response = await client.post(_INFERENCE_PATH, json=body, timeout=self._config.timeout_s)
        response.raise_for_status()
        return EmbeddingResult.model_validate(response.json())

    async def aclose(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._client is not None:
            await self._client.aclose()
            self._client = None
