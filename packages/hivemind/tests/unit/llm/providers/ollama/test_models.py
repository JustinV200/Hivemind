"""Tests for hivemind.llm.providers.ollama.models: what an Ollama server's replies parse into.

Fits into the Hive:
    Mirrors src/hivemind/llm/providers/ollama/models.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.llm.providers.ollama.models for the module under test.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from hivemind.llm.providers.ollama.models import (
    OllamaHealth,
    OllamaModel,
    OllamaProcesses,
    OllamaTags,
    OllamaVersion,
)


def test_ollama_tags_parse_a_listing_and_ignore_fields_a_newer_server_adds() -> None:
    payload = {
        "models": [
            {
                "name": "local-small:latest",
                "size": 1234,
                "digest": "abc",
                "modified_at": "2026-01-01T00:00:00Z",
                "details": {"family": "fam", "parameter_size": "7B", "quantization_level": "Q4"},
                "brand_new_field": True,
            }
        ]
    }

    tags = OllamaTags.model_validate(payload)

    (model,) = tags.models
    assert (model.name, model.size, model.details.parameter_size) == (
        "local-small:latest",
        1234,
        "7B",
    )


def test_ollama_model_defaults_every_optional_field() -> None:
    model = OllamaModel.model_validate({"name": "local-small"})

    assert model.size == 0
    assert model.details.family == ""


def test_ollama_model_rejects_a_missing_name() -> None:
    with pytest.raises(ValidationError):
        OllamaModel.model_validate({"size": 1})


def test_ollama_version_requires_the_version_field() -> None:
    assert OllamaVersion.model_validate({"version": "9.9.9"}).version == "9.9.9"
    with pytest.raises(ValidationError):
        OllamaVersion.model_validate({})


def test_ollama_processes_parse_loaded_models() -> None:
    processes = OllamaProcesses.model_validate(
        {"models": [{"name": "local-small", "size_vram": 5, "expires_at": "soon"}]}
    )

    assert processes.models[0].size_vram == 5
    assert processes.models[0].expires_at == "soon"


def test_ollama_health_round_trips_and_forbids_unknown_fields() -> None:
    health = OllamaHealth(version="1", loaded=())

    assert OllamaHealth.model_validate_json(health.model_dump_json()) == health
    with pytest.raises(ValidationError):
        OllamaHealth.model_validate({"version": "1", "loaded": [], "extra": 1})
