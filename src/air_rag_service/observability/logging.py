"""Structured logging: structlog + stdlib, one processor chain for both.

`request_id` rides on a contextvar so it reaches every log line for a request
without being threaded through call signatures.
"""

from __future__ import annotations

import logging
import sys
from collections.abc import Callable
from typing import Any, Final, TextIO

import orjson
import structlog

from air_rag_service.config import Settings

__all__ = ["bind_request_context", "clear_request_context", "configure_logging", "get_logger"]

_ADOPTED_LOGGERS: Final[tuple[str, ...]] = (
    "uvicorn",
    "uvicorn.error",
    "uvicorn.access",
    "uvicorn.asgi",
    "fastapi",
    "httpx",
    "httpcore",
)

_handler: logging.Handler | None = None


class _StdoutHandler(logging.StreamHandler[TextIO]):
    """Resolves `sys.stdout` at emit time, not at construction — needed for
    pytest's capture fixtures and uvicorn's reloader, both of which swap it."""

    def __init__(self) -> None:
        super().__init__(sys.stdout)

    @property
    def stream(self) -> TextIO:
        return sys.stdout

    @stream.setter
    def stream(self, value: TextIO) -> None:
        pass


def _json_dumps(obj: Any, default: Callable[[Any], Any] | None = None, **_: Any) -> str:
    return orjson.dumps(obj, default=default or str).decode("utf-8")


def configure_logging(settings: Settings) -> None:
    global _handler

    as_json = settings.obs.log_format == "json"
    level = logging.getLevelNamesMapping()[settings.obs.log_level]

    shared: list[structlog.typing.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.UnicodeDecoder(),
    ]

    structlog.configure(
        processors=[*shared, structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    renderer: structlog.typing.Processor = (
        structlog.processors.JSONRenderer(serializer=_json_dumps)
        if as_json
        else structlog.dev.ConsoleRenderer(colors=sys.stdout.isatty())
    )
    final: list[structlog.typing.Processor] = [
        structlog.stdlib.ProcessorFormatter.remove_processors_meta
    ]
    if as_json:
        final.append(structlog.processors.format_exc_info)
    final.append(renderer)

    handler = _StdoutHandler()
    handler.setFormatter(
        structlog.stdlib.ProcessorFormatter(
            processors=final,
            foreign_pre_chain=[*shared, structlog.stdlib.ExtraAdder()],
        )
    )

    root = logging.getLogger()
    if _handler is not None:
        root.removeHandler(_handler)
    root.addHandler(handler)
    root.setLevel(level)
    _handler = handler

    for name in _ADOPTED_LOGGERS:
        adopted = logging.getLogger(name)
        adopted.handlers.clear()
        adopted.propagate = True


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    if name is None:
        return structlog.stdlib.get_logger()
    return structlog.stdlib.get_logger(name)


def bind_request_context(**kw: Any) -> None:
    structlog.contextvars.bind_contextvars(**kw)


def clear_request_context() -> None:
    structlog.contextvars.clear_contextvars()
