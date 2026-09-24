"""Tests for hivemind.llm.providers.whisper.config: WhisperConfig.

Fits into the Hive:
    Mirrors src/hivemind/llm/providers/whisper/config.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.llm.providers.whisper.config for the module under test.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from hivemind.llm.providers.whisper.config import (
    DEFAULT_LOAD_TIMEOUT_S,
    DEFAULT_TIMEOUT_S,
    WhisperConfig,
)
from hivemind.llm.transcription import TranscriptionCapabilities


def test_whisper_config_defaults_pick_the_device_and_precision_later() -> None:
    config = WhisperConfig(model="speech-model")

    assert config.device == "auto"
    assert config.compute_type is None
    assert config.local_files_only is False
    assert config.cpu_threads == 0
    assert config.download_root is None
    assert config.timeout_s == DEFAULT_TIMEOUT_S
    assert config.load_timeout_s == DEFAULT_LOAD_TIMEOUT_S
    assert config.capabilities == TranscriptionCapabilities()


def test_whisper_config_has_no_default_model() -> None:
    with pytest.raises(ValidationError):
        WhisperConfig()  # type: ignore[call-arg]  # The missing model id is the test.


def test_whisper_config_rejects_an_empty_model_id() -> None:
    with pytest.raises(ValidationError):
        WhisperConfig(model="")


def test_whisper_config_rejects_an_unknown_device() -> None:
    # "tpu" names no device the config knows; refusing it is the whole test.
    with pytest.raises(ValidationError):
        WhisperConfig(model="m", device="tpu")


def test_whisper_config_rejects_a_malformed_compute_type() -> None:
    with pytest.raises(ValidationError):
        WhisperConfig(model="m", compute_type="Float 16")


def test_whisper_config_rejects_an_unknown_field() -> None:
    with pytest.raises(ValidationError):
        WhisperConfig(model="m", beam_size=5)  # type: ignore[call-arg]  # The extra is the test.


def test_whisper_config_round_trips_through_json() -> None:
    config = WhisperConfig(
        model="m", device="cpu", compute_type="int8", download_root=Path("weights"), cpu_threads=4
    )

    restored = WhisperConfig.model_validate_json(config.model_dump_json())

    assert restored == config
