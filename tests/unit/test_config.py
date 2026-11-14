"""Settings defaults and production guards."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from air_rag_service.config import Settings


def _settings(**overrides: object) -> Settings:
    return Settings.model_validate({"app": {"env": "test"}, **overrides})


def test_default_port_is_this_services_slot_in_the_port_map() -> None:
    assert _settings().app.port == 8087


def test_model_gateway_defaults_to_its_port_map_slot() -> None:
    assert _settings().model_gateway.base_url == "http://localhost:8083"


def test_embedding_model_defaults_to_the_published_local_768_name() -> None:
    gateway = _settings().model_gateway

    assert (gateway.embed_model, gateway.embed_dimensions) == ("local-768-v1", 768)


def test_embed_batch_size_respects_the_gateways_per_call_limit() -> None:
    with pytest.raises(ValidationError):
        _settings(model_gateway={"embed_batch_size": 2049})


def test_production_refuses_the_unauthenticated_hatch() -> None:
    with pytest.raises(ValidationError, match="allow_unauthenticated"):
        Settings.model_validate(
            {"app": {"env": "production"}, "security": {"allow_unauthenticated": True}}
        )


def test_health_timeout_is_much_shorter_than_the_turn_timeout() -> None:
    settings = _settings()

    gateway = settings.model_gateway
    assert gateway.health_timeout_s < gateway.timeout_s / 5


def test_default_top_k_is_bounded() -> None:
    with pytest.raises(ValidationError):
        Settings.model_validate({"app": {"env": "test"}, "retrieval": {"default_top_k": 0}})
