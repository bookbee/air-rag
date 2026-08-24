"""Settings defaults and production guards."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from air_rag.config import Settings


def _settings(**overrides: object) -> Settings:
    return Settings.model_validate({"app": {"env": "test"}, **overrides})


def test_default_port_is_this_services_slot_in_the_port_map() -> None:
    assert _settings().app.port == 8087


def test_llm_defaults_to_its_port_map_slot() -> None:
    assert _settings().llm.base_url == "http://localhost:8083"


def test_production_refuses_the_unauthenticated_hatch() -> None:
    with pytest.raises(ValidationError, match="allow_unauthenticated"):
        Settings.model_validate(
            {"app": {"env": "production"}, "security": {"allow_unauthenticated": True}}
        )


def test_health_timeout_is_much_shorter_than_the_turn_timeout() -> None:
    settings = _settings()

    assert settings.llm.health_timeout_s < settings.llm.timeout_s / 5


def test_default_top_k_is_bounded() -> None:
    with pytest.raises(ValidationError):
        Settings.model_validate({"app": {"env": "test"}, "retrieval": {"default_top_k": 0}})
