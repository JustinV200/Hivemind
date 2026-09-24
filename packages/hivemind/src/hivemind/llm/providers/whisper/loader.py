"""Load a faster-whisper model: the only module that imports faster_whisper or its runtime.

faster-whisper is an optional extra (`hivemind[whisper]`, ADR-0033): it pulls in CTranslate2, a
native inference runtime a terminal-only Hive never needs. So nothing imports it at module scope;
`FasterWhisperLoader.load` imports it at the moment a model is first needed, the same lazy pattern
`hivemind.hive.backends.docker.sdk_client` uses for its optional SDK, and the rest of the Hive
imports fine without the extra. Loading picks the device (the GPU when CTranslate2 counts one,
the CPU otherwise), the precision for that device, and hands back a `LoadedModel`: the model
behind the `WhisperModelLike` protocol, plus the exception types its runs may raise, sorted into
refusals (the audio or the request itself was rejected) and faults (the model could not run), so
the adapter can translate every one of them without a blanket `except`. `ModelLoader` is the seam:
the adapter takes any loader, so a test hands it a stand-in model and nothing is downloaded.

Fits into the Hive:
    Layer 1 (foundational services; capacity as data), inside `hivemind.llm.providers.whisper`.
    Called by `hivemind.llm.providers.whisper.provider.WhisperLocalTranscription` on its first
    transcription, on a worker thread (`asyncio.to_thread`), since loading reads, and may
    download, gigabytes. Calls into `faster_whisper`, `ctranslate2` and `av` (all lazily), and
    this package's `config` and `mapping`.

Key invariants:
    - `faster_whisper`, `ctranslate2` and `av` are imported only inside functions here, never at
      module scope, and nowhere else in the workspace (codingrules section 8.6; the root
      import-linter contract confines `faster_whisper` to `hivemind.llm.providers`).
    - `load` blocks; it never runs on the event loop.
    - `missing_reason` never imports the library: it only asks whether it is installed, so a
      health probe stays cheap and never loads a native runtime.

See Also:
    - docs/adr/0033-transcription-provider-whisper-first.md for the device rule and the extra.
    - hivemind.llm.providers.whisper.mapping for the constructor options and the protocols.
"""

from __future__ import annotations

import importlib.util
from dataclasses import dataclass
from typing import Protocol

from hivemind.llm.providers.whisper import mapping
from hivemind.llm.providers.whisper.config import WhisperConfig

LIBRARY_MODULE = "faster_whisper"  # The extra's import name; probed, never imported, by health.
INSTALL_HINT = (
    "faster-whisper is not installed; install the 'hivemind[whisper]' extra "
    "(e.g. `uv sync --extra whisper`) to transcribe in process"
)
# What a model load raises beyond ImportError: a missing or unreadable model (OSError, which the
# model hub's own errors subclass), an unusable device or precision (RuntimeError, ValueError).
LOAD_FAILURES: tuple[type[Exception], ...] = (OSError, RuntimeError, ValueError)
# A run the request itself doomed: an unknown language code, undecodable audio.
REFUSAL_FAILURES: tuple[type[Exception], ...] = (ValueError,)
# A run the model could not finish: device errors, exhausted memory, a vanished file handle.
FAULT_FAILURES: tuple[type[Exception], ...] = (OSError, RuntimeError, MemoryError)

__all__ = [
    "FAULT_FAILURES",
    "INSTALL_HINT",
    "LIBRARY_MODULE",
    "LOAD_FAILURES",
    "REFUSAL_FAILURES",
    "FasterWhisperLoader",
    "LoadedModel",
    "ModelLoader",
    "resolve_device",
]


@dataclass(frozen=True, slots=True)
class LoadedModel:
    """One loaded model, where it runs, and the exception types its runs may raise."""

    model: mapping.WhisperModelLike  # The model, behind the one method the adapter calls.
    device: str  # Where it runs: mapping.GPU_DEVICE or mapping.CPU_DEVICE.
    compute_type: str  # The precision it was loaded at.
    refusals: tuple[type[Exception], ...] = REFUSAL_FAILURES  # Errors the request itself caused.
    faults: tuple[type[Exception], ...] = FAULT_FAILURES  # Errors of a model that could not run.


class ModelLoader(Protocol):
    """Load a model for one WhisperConfig; the adapter's seam onto the library."""

    def missing_reason(self) -> str | None:
        """Return why no model can load here (the library is absent), or None when one can."""
        ...

    def load(self, config: WhisperConfig) -> LoadedModel:
        """Load `config.model`; blocking, so callers run it on a worker thread.

        Args:
            config: Which model, where, at what precision, and whether it may download.

        Returns:
            The loaded model and its failure types.

        Raises:
            ImportError: The library is not installed.
            OSError: The model could not be found, downloaded or read.
            RuntimeError: The device or runtime refused the model.
            ValueError: The configuration names a precision or device the runtime rejects.
        """
        ...


class FasterWhisperLoader:
    """The real ModelLoader: faster-whisper over CTranslate2, imported on first use."""

    def missing_reason(self) -> str | None:
        """Return `INSTALL_HINT` when faster-whisper is absent, None when it is installed."""
        return None if importlib.util.find_spec(LIBRARY_MODULE) is not None else INSTALL_HINT

    def load(self, config: WhisperConfig) -> LoadedModel:
        """Import faster-whisper, pick the device and precision, and load the model.

        See `ModelLoader.load` for the full contract; this blocks for as long as reading (and,
        unless `config.local_files_only`, downloading) the weights takes.
        """
        # SAFETY: the one legal faster_whisper import in the workspace (root pyproject.toml's
        # vendor-SDK contract); lazy so the rest of the Hive imports without the optional extra.
        # The ignore covers both environments: extra installed (untyped) and absent (not found).
        import faster_whisper  # type: ignore[import-untyped, import-not-found, unused-ignore]

        cuda_devices = _cuda_device_count() if config.device == "auto" else 0
        device, compute_type = resolve_device(config, cuda_devices)
        options = mapping.model_options(config, device, compute_type)
        # Blocking: reads (and may download) the weights, then builds the runtime's model.
        model: mapping.WhisperModelLike = faster_whisper.WhisperModel(config.model, **options)
        refusals = (*REFUSAL_FAILURES, *_codec_failures())
        return LoadedModel(model, device, compute_type, refusals=refusals)


def resolve_device(config: WhisperConfig, cuda_devices: int) -> tuple[str, str]:
    """Pick the device and precision for `config`: the GPU when asked or found, else the CPU.

    Args:
        config: The provider's configuration; `device` "auto" defers to `cuda_devices`.
        cuda_devices: How many GPUs the runtime can see (only read when `device` is "auto").

    Returns:
        `(device, compute_type)`: the configured precision, or the device's default
        (`mapping.default_compute_type`) when the config leaves it None.
    """
    if config.device != "auto":
        device = mapping.GPU_DEVICE if config.device == "cuda" else mapping.CPU_DEVICE
    else:
        device = mapping.GPU_DEVICE if cuda_devices > 0 else mapping.CPU_DEVICE
    compute_type = config.compute_type or mapping.default_compute_type(device)
    return device, compute_type


def _cuda_device_count() -> int:
    """Ask CTranslate2 (faster-whisper's runtime) how many GPUs it can use; 0 without CUDA."""
    # Lazy for the same reason as faster_whisper: CTranslate2 ships only with the extra.
    import ctranslate2  # type: ignore[import-untyped, import-not-found, unused-ignore]

    count: int = ctranslate2.get_cuda_device_count()
    return count


def _codec_failures() -> tuple[type[Exception], ...]:
    """Return the audio codec's own error base, so undecodable audio is a refusal, not a leak.

    faster-whisper decodes a file object with PyAV, whose error types do not all derive from a
    standard exception; naming its base class is the only way to translate every one of them
    without a blanket `except Exception` (codingrules section 10).
    """
    try:
        import av.error  # type: ignore[import-not-found, unused-ignore]
    except ImportError:
        return ()  # No codec installed, so no codec error can be raised either.
    return (av.error.FFmpegError,)
