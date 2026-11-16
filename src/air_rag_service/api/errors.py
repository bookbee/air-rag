"""RFC 9457 problem details. Every failure leaves the service as
`application/problem+json`; nothing internal escapes into a response body."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from http import HTTPStatus
from typing import Any, ClassVar, Final

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.requests import Request
from starlette.responses import Response

from air_rag_service.constants import HEADER_REQUEST_ID
from air_rag_service.observability.logging import get_logger
from air_rag_service.schemas.errors import FieldError, ProblemDetail

__all__ = [
    "AppError",
    "DependencyUnavailableError",
    "InsufficientScopeError",
    "InternalError",
    "MalformedRequestError",
    "MissingApiKeyError",
    "NotFoundError",
    "ProblemResponse",
    "RateLimitedError",
    "ValidationError",
    "install_error_handlers",
    "problem_response",
]

_log = get_logger(__name__)

PROBLEM_CONTENT_TYPE: Final[str] = "application/problem+json"
_STRIPPED_LOC_PREFIXES: Final[frozenset[str]] = frozenset({"body"})


class AppError(Exception):
    slug: ClassVar[str] = "internal-error"
    title: ClassVar[str] = "Internal server error"
    status: ClassVar[int] = 500

    def __init__(
        self,
        detail: str | None = None,
        *,
        errors: Sequence[FieldError] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        super().__init__(detail or self.title)
        self.detail = detail
        self.errors: list[FieldError] = list(errors or ())
        self.headers: dict[str, str] = dict(headers or {})

    def to_problem(
        self, *, instance: str | None = None, request_id: str | None = None
    ) -> ProblemDetail:
        return ProblemDetail.build(
            slug=self.slug,
            title=self.title,
            status=self.status,
            detail=self.detail,
            instance=instance,
            request_id=request_id,
            errors=self.errors,
        )


class MalformedRequestError(AppError):
    slug = "malformed-request"
    title = "Malformed request"
    status = 400


class MissingApiKeyError(AppError):
    slug = "missing-api-key"
    title = "Missing or invalid API key"
    status = 401


class InsufficientScopeError(AppError):
    slug = "insufficient-scope"
    title = "Insufficient scope"
    status = 403


class NotFoundError(AppError):
    slug = "not-found"
    title = "Not found"
    status = 404


class ValidationError(AppError):
    slug = "validation-error"
    title = "Request validation failed"
    status = 422


class RateLimitedError(AppError):
    slug = "rate-limited"
    title = "Rate limit exceeded"
    status = 429


class InternalError(AppError):
    slug = "internal-error"
    title = "Internal server error"
    status = 500


class DependencyUnavailableError(AppError):
    """air-model-gateway is unreachable, so a query can't be embedded."""

    slug = "dependency-unavailable"
    title = "Required dependency unavailable"
    status = 503


_BY_STATUS: Final[dict[int, type[AppError]]] = {
    cls.status: cls
    for cls in (
        MalformedRequestError,
        MissingApiKeyError,
        InsufficientScopeError,
        NotFoundError,
        ValidationError,
        RateLimitedError,
        InternalError,
        DependencyUnavailableError,
    )
}


class ProblemResponse(JSONResponse):
    media_type = PROBLEM_CONTENT_TYPE


def problem_response(
    problem: ProblemDetail, *, headers: Mapping[str, str] | None = None
) -> ProblemResponse:
    merged = dict(headers or {})
    if problem.request_id:
        merged.setdefault(HEADER_REQUEST_ID, problem.request_id)
    return ProblemResponse(
        status_code=problem.status,
        content=problem.model_dump(mode="json", exclude_none=True),
        headers=merged,
    )


def _instance(request: Request) -> str:
    return request.url.path


def _request_id(request: Request) -> str | None:
    value = getattr(request.state, "request_id", None)
    return value if isinstance(value, str) else None


def _slug_for_status(status: int) -> tuple[str, str]:
    known = _BY_STATUS.get(status)
    if known is not None:
        return known.slug, known.title
    try:
        phrase = HTTPStatus(status).phrase
    except ValueError:
        phrase = "HTTP error"
    return re.sub(r"[^a-z0-9]+", "-", phrase.lower()).strip("-"), phrase


async def _app_error_handler(request: Request, exc: Exception) -> Response:
    assert isinstance(exc, AppError)
    problem = exc.to_problem(instance=_instance(request), request_id=_request_id(request))
    return problem_response(problem, headers=exc.headers)


async def _validation_error_handler(request: Request, exc: Exception) -> Response:
    """Body validation runs before a route's dependencies, so without the first
    check a caller with no key would learn about its body before being asked
    who it is. Authentication comes first: a keyed route answers a bad body
    with the same 401 `require_principal` would have given (SIT RG-001)."""
    assert isinstance(exc, RequestValidationError)
    if _requires_principal(request) and not _has_valid_key(request):
        return await _app_error_handler(
            request, MissingApiKeyError("A valid X-API-Key header is required.")
        )
    if any(raw.get("type") == "json_invalid" for raw in exc.errors()):
        # Not one field's error: the body could not be read at all.
        return await _app_error_handler(
            request, MalformedRequestError("The request body is not valid JSON.")
        )
    field_errors = [_as_field_error(raw) for raw in exc.errors()]
    first = field_errors[0] if field_errors else None
    detail = f"{first.field}: {first.message}" if first and first.message else None
    return await _app_error_handler(request, ValidationError(detail, errors=field_errors))


def _requires_principal(request: Request) -> bool:
    """Whether the matched route depends, at any depth, on `require_principal`."""
    from air_rag_service.api.deps import require_principal  # deps imports this module

    dependant = getattr(request.scope.get("route"), "dependant", None)
    pending = [dependant] if dependant is not None else []
    while pending:
        current = pending.pop()
        if current.call is require_principal:
            return True
        pending.extend(current.dependencies)
    return False


def _has_valid_key(request: Request) -> bool:
    from air_rag_service.api.deps import get_app_state  # deps imports this module

    return get_app_state(request).key_store.lookup(request.headers.get("X-API-Key")) is not None


async def _http_exception_handler(request: Request, exc: Exception) -> Response:
    assert isinstance(exc, StarletteHTTPException)
    slug, title = _slug_for_status(exc.status_code)
    problem = ProblemDetail.build(
        slug=slug,
        title=title,
        status=exc.status_code,
        detail=str(exc.detail) if exc.detail else None,
        instance=_instance(request),
        request_id=_request_id(request),
    )
    return problem_response(problem, headers=exc.headers or {})


async def _unhandled_exception_handler(request: Request, exc: Exception) -> Response:
    _log.error(
        "request.unhandled_exception",
        exc_info=exc,
        method=request.method,
        path=_instance(request),
        error_type=type(exc).__name__,
    )
    return await _app_error_handler(
        request,
        InternalError("An unexpected error occurred. Quote the request id when reporting."),
    )


def _as_field_error(raw: Mapping[str, Any]) -> FieldError:
    loc: tuple[Any, ...] = tuple(raw.get("loc", ()))
    if loc and loc[0] in _STRIPPED_LOC_PREFIXES:
        loc = loc[1:]
    return FieldError(
        field=".".join(str(part) for part in loc) or "__root__",
        code=str(raw.get("type", "invalid")),
        message=str(raw["msg"]) if raw.get("msg") else None,
    )


def install_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(AppError, _app_error_handler)
    app.add_exception_handler(RequestValidationError, _validation_error_handler)
    app.add_exception_handler(StarletteHTTPException, _http_exception_handler)
    app.add_exception_handler(Exception, _unhandled_exception_handler)
