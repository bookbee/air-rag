"""Stable identifiers shared across the service.

Public surface: these values appear in API responses, metric labels, and
config keys. Renaming one is a breaking change.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Final

HEADER_API_KEY: Final[str] = "X-API-Key"
HEADER_REQUEST_ID: Final[str] = "X-Request-ID"

ERROR_TYPE_BASE: Final[str] = "https://air.dev/problems/air-rag"

SCOPE_RETRIEVAL_READ: Final[str] = "retrieval:read"
SCOPE_ADMIN_READ: Final[str] = "admin:read"

ALL_SCOPES: Final[frozenset[str]] = frozenset({SCOPE_RETRIEVAL_READ, SCOPE_ADMIN_READ})


class DownstreamService(StrEnum):
    """Services this one calls. Values match repo names, for metric labels."""

    LLM = "air-llm"
