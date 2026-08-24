"""Prometheus metrics. Every label value comes from a closed enum or config —
never a caller-supplied string."""

from __future__ import annotations

from typing import Final

from prometheus_client import CollectorRegistry, Counter, Histogram, generate_latest

from air_rag.constants import DownstreamService

__all__ = ["CONTENT_TYPE", "REGISTRY", "record_downstream_call", "record_retrieval", "render"]

REGISTRY: Final[CollectorRegistry] = CollectorRegistry()

CONTENT_TYPE: Final[str] = "text/plain; version=0.0.4; charset=utf-8"

_RETRIEVAL_BUCKETS: Final[tuple[float, ...]] = (0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.0, 5.0)

_REQUESTS = Counter(
    "air_rag_requests_total",
    "HTTP requests, by route template and status.",
    ["endpoint", "status", "key_id"],
    registry=REGISTRY,
)

_REQUEST_SECONDS = Histogram(
    "air_rag_request_seconds",
    "HTTP request duration.",
    ["endpoint"],
    registry=REGISTRY,
)

_RETRIEVALS = Counter(
    "air_rag_retrievals_total",
    "Retrieval calls, by outcome.",
    ["outcome"],
    registry=REGISTRY,
)

_RETRIEVAL_SECONDS = Histogram(
    "air_rag_retrieval_seconds",
    "Retrieval call duration.",
    buckets=_RETRIEVAL_BUCKETS,
    registry=REGISTRY,
)

_DOWNSTREAM_CALLS = Counter(
    "air_rag_downstream_calls_total",
    "Calls to other AIR services, by outcome.",
    ["service", "outcome"],
    registry=REGISTRY,
)


def record_request(*, endpoint: str, status: int, key_id: str, duration_s: float) -> None:
    _REQUESTS.labels(endpoint, str(status), key_id).inc()
    _REQUEST_SECONDS.labels(endpoint).observe(duration_s)


def record_retrieval(*, outcome: str, duration_s: float) -> None:
    """`outcome`: `hit` | `empty` | `error`."""
    _RETRIEVALS.labels(outcome).inc()
    _RETRIEVAL_SECONDS.observe(duration_s)


def record_downstream_call(*, service: DownstreamService, outcome: str) -> None:
    _DOWNSTREAM_CALLS.labels(service.value, outcome).inc()


def render() -> bytes:
    return generate_latest(REGISTRY)
