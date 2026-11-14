"""Request-scoped middleware: identity, access logging, a body ceiling.

Raw ASGI middleware, not `BaseHTTPMiddleware` — the same reasoning
air-platform documents: `BaseHTTPMiddleware` runs the app in a child task that
gets a *copy* of the contextvar context, so anything bound downstream would be
invisible to `clear_request_context()` back in the parent.
"""

from __future__ import annotations

import re
import time
import uuid
from typing import Final

from fastapi import FastAPI
from starlette.datastructures import Headers, MutableHeaders
from starlette.requests import Request
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from air_rag_service.api.errors import MalformedRequestError, problem_response
from air_rag_service.config import Settings
from air_rag_service.constants import HEADER_REQUEST_ID
from air_rag_service.observability import metrics
from air_rag_service.observability.logging import (
    bind_request_context,
    clear_request_context,
    get_logger,
)

__all__ = ["KEY_ID_ATTR", "install_middleware", "new_request_id", "resolve_request_id"]

_log = get_logger(__name__)

_REQUEST_ID_PATTERN: Final[re.Pattern[str]] = re.compile(r"\A[A-Za-z0-9._:-]{1,128}\Z")
_REQUEST_ID_PREFIX: Final[str] = "req_"
_UNMATCHED_ENDPOINT: Final[str] = "unmatched"
_ANONYMOUS_KEY_ID: Final[str] = "anonymous"

KEY_ID_ATTR: Final[str] = "key_id"


def new_request_id() -> str:
    return f"{_REQUEST_ID_PREFIX}{uuid.uuid4().hex}"


def resolve_request_id(supplied: str | None) -> str:
    if supplied is not None and _REQUEST_ID_PATTERN.match(supplied):
        return supplied
    return new_request_id()


class RequestContextMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_id = resolve_request_id(Headers(scope=scope).get(HEADER_REQUEST_ID))
        scope.setdefault("state", {})
        scope["state"]["request_id"] = request_id
        bind_request_context(request_id=request_id)

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                MutableHeaders(scope=message).setdefault(HEADER_REQUEST_ID, request_id)
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            clear_request_context()


class AccessLogMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        started = time.perf_counter()
        status = 500

        async def send_wrapper(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = int(message["status"])
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            self._record(scope, status=status, duration_s=time.perf_counter() - started)

    def _record(self, scope: Scope, *, status: int, duration_s: float) -> None:
        state = scope.get("state") or {}
        key_id = state.get(KEY_ID_ATTR) or _ANONYMOUS_KEY_ID
        request_id = state.get("request_id")

        _log.info(
            "http.access",
            method=scope.get("method", ""),
            path=scope.get("path", ""),
            status=status,
            duration_ms=round(duration_s * 1000, 3),
            key_id=key_id,
            request_id=request_id,
        )
        metrics.record_request(
            endpoint=self._endpoint(scope), status=status, key_id=key_id, duration_s=duration_s
        )

    @staticmethod
    def _endpoint(scope: Scope) -> str:
        route = scope.get("route")
        relative = getattr(route, "path", None)
        if not isinstance(relative, str):
            return _UNMATCHED_ENDPOINT

        template = scope.get("path")
        if not isinstance(template, str) or not template:
            return relative

        for name, value in (scope.get("path_params") or {}).items():
            rendered = str(value)
            if not rendered or rendered not in template:
                return relative
            template = template.replace(rendered, f"{{{name}}}", 1)
        return template


class _BodyTooLargeError(Exception):
    pass


class BodySizeLimitMiddleware:
    def __init__(self, app: ASGIApp, *, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        declared = Headers(scope=scope).get("content-length")
        if declared is not None:
            try:
                length = int(declared)
            except ValueError:
                await self._reject(scope, receive, send, "Content-Length is not an integer.")
                return
            if length > self.max_bytes:
                await self._reject(scope, receive, send, self._too_large_detail(length))
                return

        received = 0
        started = False

        async def receive_wrapper() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes:
                    raise _BodyTooLargeError
            return message

        async def send_wrapper(message: Message) -> None:
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, receive_wrapper, send_wrapper)
        except _BodyTooLargeError:
            if started:
                raise
            await self._reject(scope, receive, send, self._too_large_detail(received))

    def _too_large_detail(self, size: int) -> str:
        return f"Request body of {size} bytes exceeds the {self.max_bytes} byte limit."

    async def _reject(self, scope: Scope, receive: Receive, send: Send, detail: str) -> None:
        request = Request(scope)
        error = MalformedRequestError(detail)
        problem = error.to_problem(
            instance=request.url.path,
            request_id=getattr(request.state, "request_id", None),
        )
        await problem_response(problem)(scope, receive, send)


def install_middleware(app: FastAPI, settings: Settings) -> None:
    app.add_middleware(BodySizeLimitMiddleware, max_bytes=settings.app.max_body_bytes)
    app.add_middleware(AccessLogMiddleware)
    app.add_middleware(RequestContextMiddleware)
