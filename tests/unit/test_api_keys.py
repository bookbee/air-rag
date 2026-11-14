"""API key identity: every key carries a tenant, and nothing falls back across tenants."""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from air_rag.config import Settings
from air_rag.security.api_keys import ApiKeyStore, hash_key

_SALT = "s"


def _settings(records: list[dict[str, object]], **security: object) -> Settings:
    return Settings.model_validate(
        {
            "app": {"env": "test"},
            "security": {"hash_salt": _SALT, "api_keys_inline": json.dumps(records), **security},
        }
    )


def _record(**overrides: object) -> dict[str, object]:
    return {
        "id": "k",
        "name": "key",
        "key_hash": hash_key("raw", _SALT),
        "tenant": "brightcart",
        "scopes": ["retrieval:read"],
        **overrides,
    }


def test_a_key_carries_its_tenant_into_the_principal() -> None:
    store = ApiKeyStore.load(_settings([_record()]))

    record = store.lookup("raw")

    assert record is not None
    assert record.to_principal().tenant == "brightcart"


def test_a_key_without_a_tenant_fails_startup() -> None:
    record = _record()
    del record["tenant"]

    with pytest.raises(ValueError, match="tenant"):
        ApiKeyStore.load(_settings([record]))


def test_a_tenant_must_be_a_lowercase_slug() -> None:
    with pytest.raises(ValueError, match="tenant"):
        ApiKeyStore.load(_settings([_record(tenant="Bright Cart")]))


def test_unauthenticated_mode_requires_an_anonymous_tenant() -> None:
    with pytest.raises(ValidationError, match="anonymous_tenant"):
        _settings([], allow_unauthenticated=True)


def test_an_anonymous_caller_gets_only_the_anonymous_tenant() -> None:
    store = ApiKeyStore.load(_settings([], allow_unauthenticated=True, anonymous_tenant="sandbox"))

    record = store.lookup(None)

    assert record is not None
    assert record.to_principal().tenant == "sandbox"
