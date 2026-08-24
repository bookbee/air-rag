"""RFC 9457 problem details. Kept in `schemas` rather than `api` so a response
model can embed a problem without importing the HTTP layer."""

from __future__ import annotations

from pydantic import BaseModel, Field

from air_rag.constants import ERROR_TYPE_BASE


class FieldError(BaseModel):
    field: str
    code: str
    message: str | None = None


class ProblemDetail(BaseModel):
    type: str = Field(description="Absolute URI identifying the problem class.")
    title: str
    status: int = Field(ge=100, le=599)
    detail: str | None = None
    instance: str | None = None
    request_id: str | None = None
    errors: list[FieldError] = Field(default_factory=list)

    @classmethod
    def build(
        cls,
        *,
        slug: str,
        title: str,
        status: int,
        detail: str | None = None,
        instance: str | None = None,
        request_id: str | None = None,
        errors: list[FieldError] | None = None,
    ) -> ProblemDetail:
        return cls(
            type=f"{ERROR_TYPE_BASE}/{slug}",
            title=title,
            status=status,
            detail=detail,
            instance=instance,
            request_id=request_id,
            errors=errors or [],
        )
