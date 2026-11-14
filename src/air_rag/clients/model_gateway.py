"""air-model-gateway client — the one path ingestion and query-time embedding
both go through.

Reached over HTTP, not imported as a package — the precedent every AIR
service follows for the gateway. The contract is the gateway's
`POST /v1/embed` (`docs/04-integration.md` §3 in air-model-gateway):
`{"model": "<published embedding name>", "input": [...]}` in, one vector per
text out, with `embedding_model` and `dimensions` echoed back.

The embedding model name is this index's identity. A name never changes
meaning, so every response is checked against the configured name and
dimensions: a mismatch would silently corrupt the index, so it is an error,
never a fallback. A model change arrives as a new name (`…-v2`) and means a
re-index.

`probe()` checks the specific embedding model, not just the gateway process:
the gateway's own `/v1/ready` is green while *any* provider answers, which
says nothing about the one provider behind our model.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Final

import httpx
import structlog
from pydantic import BaseModel, ConfigDict

from air_rag.config import Settings
from air_rag.constants import HEADER_API_KEY, HEADER_REQUEST_ID, DownstreamService
from air_rag.observability import metrics
from air_rag.observability.logging import get_logger
from air_rag.schemas.common import DependencyStatus

__all__ = ["EmbeddingResult", "ModelGatewayClient", "ModelGatewayError"]

logger = get_logger(__name__)

_READY_PATH: Final[str] = "/v1/ready"
_CAPABILITIES_PATH: Final[str] = "/v1/capabilities"
_EMBED_PATH: Final[str] = "/v1/embed"
_SERVICE: Final[DownstreamService] = DownstreamService.MODEL_GATEWAY


class ModelGatewayError(Exception):
    """The gateway could not produce usable vectors: unreachable, an error
    status, or a response that doesn't match this index's embedding model."""


class EmbeddingResult(BaseModel):
    model_config = ConfigDict(extra="ignore")

    embeddings: list[list[float]] = []
    embedding_model: str
    dimensions: int
    cost_usd: float = 0.0


class ModelGatewayClient:
    def __init__(self, settings: Settings) -> None:
        self._config = settings.model_gateway
        self._client: httpx.AsyncClient | None = None
        self._lock = asyncio.Lock()
        self._closed = False

    @property
    def embed_model(self) -> str:
        return self._config.embed_model

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

    # ── readiness ──────────────────────────────────────────────────────────

    async def probe(self) -> DependencyStatus:
        """Can the configured embedding model serve right now? Never raises."""
        client = await self._http()
        started = time.perf_counter()

        def status(reachable: bool, detail: str | None, outcome: str) -> DependencyStatus:
            metrics.record_downstream_call(service=_SERVICE, outcome=outcome)
            return DependencyStatus(
                service=_SERVICE,
                configured=self._config.api_key is not None,
                reachable=reachable,
                latency_ms=round((time.perf_counter() - started) * 1000, 3),
                detail=detail,
            )

        timeout = self._config.health_timeout_s
        try:
            caps = await client.get(_CAPABILITIES_PATH, timeout=timeout)
            if caps.status_code != httpx.codes.OK:
                return status(False, _status_detail(caps), "error")
            problem, provider = self._check_capabilities(caps.json())
            if problem is not None:
                return status(False, problem, "error")

            ready = await client.get(_READY_PATH, timeout=timeout)
            providers = ready.json().get("providers", {}) if ready.content else {}
            if not providers.get(provider):
                return status(
                    False,
                    f"{self._config.embed_model}'s provider ({provider}) is not answering "
                    "at the gateway",
                    "error",
                )
            return status(True, None, "ok")
        except httpx.TimeoutException:
            return status(False, f"no response within {timeout}s", "timeout")
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning("model_gateway.probe_failed", error=type(exc).__name__)
            return status(False, f"connection failed ({type(exc).__name__})", "unavailable")

    def _check_capabilities(self, body: dict[str, Any]) -> tuple[str | None, str]:
        name = self._config.embed_model
        entry = (body.get("embed") or {}).get(name)
        if entry is None:
            return f"embedding model {name} is not published by the gateway", ""
        provider = str(entry.get("provider", ""))
        if not entry.get("allowed"):
            return f"this service is not granted embedding model {name}", provider
        if not entry.get("available"):
            return f"the gateway cannot serve {name} in this deployment", provider
        if entry.get("dimensions") != self._config.embed_dimensions:
            return (
                f"{name} has {entry.get('dimensions')} dimensions; this index expects "
                f"{self._config.embed_dimensions}",
                provider,
            )
        return None, provider

    # ── embeddings ─────────────────────────────────────────────────────────

    async def embed(self, texts: list[str]) -> EmbeddingResult:
        """One vector per text, in order. Splits into batches under the
        gateway's per-call limit. Raises `ModelGatewayError` on any failure —
        the caller (retrieval or ingestion) decides its own fallback; unlike
        `probe()`, this does not swallow the error."""
        name = self._config.embed_model
        if not texts:
            return EmbeddingResult(embedding_model=name, dimensions=self._config.embed_dimensions)

        client = await self._http()
        headers = _request_id_header()
        size = self._config.embed_batch_size
        vectors: list[list[float]] = []
        cost = 0.0
        for start in range(0, len(texts), size):
            batch = texts[start : start + size]
            try:
                response = await client.post(
                    _EMBED_PATH, json={"model": name, "input": batch}, headers=headers
                )
            except httpx.HTTPError as exc:
                metrics.record_downstream_call(service=_SERVICE, outcome="unavailable")
                raise ModelGatewayError(
                    f"could not reach the gateway ({type(exc).__name__})"
                ) from exc
            if response.is_error:
                metrics.record_downstream_call(service=_SERVICE, outcome="error")
                raise ModelGatewayError(_status_detail(response))
            result = EmbeddingResult.model_validate(response.json())
            self._check_result(result, expected=len(batch))
            metrics.record_downstream_call(service=_SERVICE, outcome="ok")
            vectors.extend(result.embeddings)
            cost += result.cost_usd

        return EmbeddingResult(
            embeddings=vectors,
            embedding_model=name,
            dimensions=self._config.embed_dimensions,
            cost_usd=cost,
        )

    def _check_result(self, result: EmbeddingResult, *, expected: int) -> None:
        cfg = self._config
        if result.embedding_model != cfg.embed_model or result.dimensions != cfg.embed_dimensions:
            raise ModelGatewayError(
                f"gateway answered with {result.embedding_model} ({result.dimensions}d); "
                f"this index is {cfg.embed_model} ({cfg.embed_dimensions}d)"
            )
        if len(result.embeddings) != expected or any(
            len(v) != cfg.embed_dimensions for v in result.embeddings
        ):
            raise ModelGatewayError("gateway returned a malformed embedding batch")

    async def aclose(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._client is not None:
            await self._client.aclose()
            self._client = None


def _request_id_header() -> dict[str, str]:
    """Forward our request id, so one id follows a call through our logs and
    the gateway's audit line."""
    request_id = structlog.contextvars.get_contextvars().get("request_id")
    return {HEADER_REQUEST_ID: request_id} if isinstance(request_id, str) else {}


def _status_detail(response: httpx.Response) -> str:
    """The gateway's problem `code`, never its body verbatim."""
    code = None
    try:
        body = response.json()
        code = body.get("code") if isinstance(body, dict) else None
    except ValueError:
        pass
    suffix = f" ({code})" if code else ""
    return f"air-model-gateway returned HTTP {response.status_code}{suffix}"
