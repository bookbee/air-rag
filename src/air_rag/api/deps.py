"""Request dependencies: who is calling, and may they call this?"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Final

from fastapi import Depends, Header, Request

from air_rag.api.errors import InsufficientScopeError, MissingApiKeyError
from air_rag.api.middleware import KEY_ID_ATTR
from air_rag.clients.llm import LlmClient
from air_rag.config import Settings
from air_rag.observability.logging import bind_request_context, get_logger
from air_rag.retrieval.base import VectorStore
from air_rag.schemas.common import Principal
from air_rag.security.api_keys import ApiKeyStore

__all__ = [
    "AppState",
    "get_app_state",
    "require_principal",
    "require_scope",
]

_log = get_logger(__name__)

STATE_ATTR: Final[str] = "air_state"


@dataclass(frozen=True, slots=True)
class AppState:
    settings: Settings
    key_store: ApiKeyStore
    llm: LlmClient
    store: VectorStore


def get_app_state(request: Request) -> AppState:
    state = getattr(request.app.state, STATE_ATTR, None)
    if not isinstance(state, AppState):  # pragma: no cover — a wiring defect
        raise RuntimeError("application state is not installed; build the app with create_app()")
    return state


async def require_principal(
    request: Request,
    state: Annotated[AppState, Depends(get_app_state)],
    api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
) -> Principal:
    record = state.key_store.lookup(api_key)
    if record is None:
        raise MissingApiKeyError("A valid X-API-Key header is required.")

    principal = record.to_principal()
    request.state.__setattr__(KEY_ID_ATTR, principal.key_id)
    bind_request_context(key_id=principal.key_id)
    return principal


def require_scope(scope: str) -> object:
    async def guard(
        principal: Annotated[Principal, Depends(require_principal)],
    ) -> Principal:
        if not principal.has_scope(scope):
            _log.info("authz.scope_denied", key_id=principal.key_id, required=scope)
            raise InsufficientScopeError(f"This key lacks the '{scope}' scope.")
        return principal

    return Depends(guard)
