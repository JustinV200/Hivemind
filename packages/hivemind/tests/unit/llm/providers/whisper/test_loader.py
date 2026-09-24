"""Tests for hivemind.llm.providers.whisper.loader: device choice and the real library's edge.

The device rule is pure and tested directly. The real loader is only exercised as far as it can
go with no model on disk and no network: with the `whisper` extra installed, loading a model id
that is not cached under `local_files_only` must fail with one of `LOAD_FAILURES` (proving the
lazy imports and the constructor options reach the library); without the extra, it must fail with
`ImportError`. Nothing here ever downloads.

Fits into the Hive:
    Mirrors src/hivemind/llm/providers/whisper/loader.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.llm.providers.whisper.loader for the module under test.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
from builders.audio import StandInWhisperModel

from hivemind.llm.providers.whisper.config import WhisperConfig
from hivemind.llm.providers.whisper.loader import (
    FAULT_FAILURES,
    INSTALL_HINT,
    LIBRARY_MODULE,
    LOAD_FAILURES,
    REFUSAL_FAILURES,
    FasterWhisperLoader,
    LoadedModel,
    resolve_device,
)
from hivemind.llm.providers.whisper.mapping import (
    CPU_COMPUTE_TYPE,
    CPU_DEVICE,
    GPU_COMPUTE_TYPE,
    GPU_DEVICE,
)

# Whether this environment has the optional `whisper` extra; CI's default job does not.
_EXTRA_INSTALLED = importlib.util.find_spec(LIBRARY_MODULE) is not None
_UNCACHED_MODEL = "hivemind-tests/no-such-speech-model"  # A repo id no cache ever holds.


def test_resolve_device_takes_the_cpu_at_int8_when_auto_finds_no_gpu() -> None:
    assert resolve_device(WhisperConfig(model="m"), 0) == (CPU_DEVICE, CPU_COMPUTE_TYPE)


def test_resolve_device_takes_the_gpu_at_half_precision_when_auto_finds_one() -> None:
    assert resolve_device(WhisperConfig(model="m"), 1) == (GPU_DEVICE, GPU_COMPUTE_TYPE)


def test_resolve_device_honours_an_explicit_cpu_even_with_a_gpu_present() -> None:
    assert resolve_device(WhisperConfig(model="m", device="cpu"), 2)[0] == CPU_DEVICE


def test_resolve_device_honours_an_explicit_gpu_and_precision() -> None:
    config = WhisperConfig(model="m", device="cuda", compute_type="int8_float16")

    assert resolve_device(config, 0) == (GPU_DEVICE, "int8_float16")


def test_loaded_model_defaults_sort_run_failures_into_refusals_and_faults() -> None:
    loaded = LoadedModel(model=StandInWhisperModel(), device=CPU_DEVICE, compute_type="int8")

    assert loaded.refusals == REFUSAL_FAILURES
    assert loaded.faults == FAULT_FAILURES


def test_missing_reason_names_the_extra_exactly_when_it_is_absent() -> None:
    reason = FasterWhisperLoader().missing_reason()

    assert reason == (None if _EXTRA_INSTALLED else INSTALL_HINT)


@pytest.mark.skipif(_EXTRA_INSTALLED, reason="the whisper extra is installed here")
def test_load_raises_import_error_without_the_extra() -> None:
    with pytest.raises(ImportError):
        FasterWhisperLoader().load(WhisperConfig(model=_UNCACHED_MODEL))


@pytest.mark.skipif(not _EXTRA_INSTALLED, reason="needs the whisper extra")
def test_load_reaches_the_library_and_fails_typed_for_an_uncached_model(tmp_path: Path) -> None:
    # local_files_only: the library looks only in tmp_path's empty cache, never the network;
    # device "auto" also asks the runtime how many GPUs it sees on the way there.
    config = WhisperConfig(model=_UNCACHED_MODEL, local_files_only=True, download_root=tmp_path)

    with pytest.raises(LOAD_FAILURES):
        FasterWhisperLoader().load(config)
