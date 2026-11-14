"""API key identity.

A raw key exists in exactly two places: the caller's `X-API-Key` header and
whatever handed the key to them. Configuration carries salted sha256 digests
only. Built once at startup, read-only afterwards.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from collections.abc import Iterable
from typing import Final, Self

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from air_rag.config import Settings
from air_rag.constants import TENANT_PATTERN
from air_rag.observability.logging import get_logger
from air_rag.schemas.common import Principal

logger = get_logger(__name__)

KEY_PREFIX: Final[str] = "airr_"
ANONYMOUS_KEY_ID: Final[str] = "anonymous"

_SHA256_HEX_LEN: Final[int] = 64
_HEX_DIGITS: Final[frozenset[str]] = frozenset("0123456789abcdef")
_DEFAULT_KEY_BYTES: Final[int] = 32


def hash_key(raw: str, salt: str) -> str:
    return hashlib.sha256((salt + raw).encode("utf-8")).hexdigest()


def generate_key(salt: str, *, nbytes: int = _DEFAULT_KEY_BYTES) -> tuple[str, str]:
    raw = KEY_PREFIX + secrets.token_urlsafe(nbytes)
    return raw, hash_key(raw, salt)


def _is_sha256_hex(value: str) -> bool:
    return len(value) == _SHA256_HEX_LEN and all(char in _HEX_DIGITS for char in value)


class ApiKeyRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=128)
    key_hash: str = Field(repr=False)
    #: Required: a key without a tenant fails startup (fail closed).
    tenant: str = Field(pattern=TENANT_PATTERN)
    scopes: set[str] = Field(default_factory=set)
    enabled: bool = True

    @field_validator("key_hash")
    @classmethod
    def _normalise_hash(cls, value: str) -> str:
        return value.strip().lower()

    def __repr__(self) -> str:
        return f"ApiKeyRecord(id={self.id!r}, name={self.name!r}, tenant={self.tenant!r})"

    def __str__(self) -> str:
        return self.__repr__()

    def has_scope(self, scope: str) -> bool:
        return scope in self.scopes

    def to_principal(self) -> Principal:
        return Principal(
            key_id=self.id, name=self.name, tenant=self.tenant, scopes=frozenset(self.scopes)
        )

    @classmethod
    def anonymous(cls, tenant: str) -> Self:
        from air_rag.constants import ALL_SCOPES

        return cls(
            id=ANONYMOUS_KEY_ID,
            name="anonymous",
            key_hash="",
            tenant=tenant,
            scopes=set(ALL_SCOPES),
        )


class ApiKeyStore:
    def __init__(
        self,
        records: Iterable[ApiKeyRecord],
        *,
        salt: str,
        allow_unauthenticated: bool = False,
        anonymous_tenant: str | None = None,
    ) -> None:
        if allow_unauthenticated and anonymous_tenant is None:
            raise ValueError("allow_unauthenticated needs an anonymous_tenant")
        self._anonymous_tenant = anonymous_tenant
        self._salt = salt
        self._allow_unauthenticated = allow_unauthenticated
        self._by_hash: dict[str, ApiKeyRecord] = {}
        for record in records:
            self._index(record)
        if not self._by_hash and not allow_unauthenticated:
            logger.warning("api_key_store_empty")

    def __len__(self) -> int:
        return len(self._by_hash)

    @classmethod
    def load(cls, settings: Settings) -> Self:
        security = settings.security
        payload: str | None = None
        source = "none"

        if security.api_keys_inline:
            payload, source = security.api_keys_inline, "inline"
        elif security.api_keys_file is not None:
            try:
                payload = security.api_keys_file.read_text(encoding="utf-8")
            except OSError as exc:
                raise ValueError(f"could not read security.api_keys_file: {exc}") from exc
            source = "file"

        records = _parse_records(payload) if payload else []
        logger.info(
            "api_key_store_loaded",
            source=source,
            count=len(records),
            allow_unauthenticated=security.allow_unauthenticated,
        )
        return cls(
            records,
            salt=security.hash_salt.get_secret_value(),
            allow_unauthenticated=security.allow_unauthenticated,
            anonymous_tenant=security.anonymous_tenant,
        )

    def lookup(self, raw_key: str | None) -> ApiKeyRecord | None:
        if raw_key:
            record = self._match(raw_key)
            if record is not None:
                if not record.enabled:
                    logger.warning("api_key_disabled", key_id=record.id)
                    return None
                return record
        if self._allow_unauthenticated and self._anonymous_tenant is not None:
            return ApiKeyRecord.anonymous(self._anonymous_tenant)
        return None

    def _match(self, raw_key: str) -> ApiKeyRecord | None:
        candidate = hash_key(raw_key, self._salt)
        record = self._by_hash.get(candidate)
        if record is None:
            return None
        if not hmac.compare_digest(record.key_hash, candidate):
            return None
        return record

    def _index(self, record: ApiKeyRecord) -> None:
        digest = record.key_hash
        if not _is_sha256_hex(digest):
            logger.warning("api_key_hash_malformed", key_id=record.id)
            return
        existing = self._by_hash.get(digest)
        if existing is not None:
            logger.warning("api_key_hash_duplicate", key_id=record.id, shadowed=existing.id)
        self._by_hash[digest] = record


def _parse_records(payload: str) -> list[ApiKeyRecord]:
    try:
        data = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"api key config is not valid JSON: {exc.msg} at position {exc.pos}"
        ) from exc
    if not isinstance(data, list):
        raise ValueError("api key config must be a JSON array of key records")

    records: list[ApiKeyRecord] = []
    for index, item in enumerate(data):
        try:
            records.append(ApiKeyRecord.model_validate(item))
        except ValidationError as exc:
            fields = ", ".join(".".join(str(part) for part in err["loc"]) for err in exc.errors())
            raise ValueError(f"api key record {index} is invalid; check fields: {fields}") from None
    return records
