"""Translate between faster-whisper's own objects and HiveMind's: the one place their names appear.

Codingrules section 8.6 gives every adapter a `mapping.py` that is the only file where the vendor's
or library's field names appear, so no library type leaves the adapter (ADR-0033). For the
in-process Whisper adapter the "wire" is faster-whisper's Python objects: the options its model
constructor takes, and the segment and info objects its `transcribe` hands back. This module
describes the slice of that surface the adapter uses as structural protocols (`WhisperModelLike`,
`SegmentLike`, `InfoLike`), so a test can stand a plain object in for a real model, and converts
the library's segments into `hivemind.llm.transcription.Transcript` values: text trimmed, times
clamped so a segment never ends before it starts, and the average token log-probability turned
into a 0-to-1 confidence.

Fits into the Hive:
    Layer 1 (foundational services; capacity as data), inside `hivemind.llm.providers.whisper`.
    Called by `hivemind.llm.providers.whisper.loader` (constructor options, the device's default
    precision) and `hivemind.llm.providers.whisper.provider` (turning a result into a
    Transcript). Calls into `hivemind.llm.transcription` and this package's `config` only; it
    imports no library itself.

Key invariants:
    - `to_transcript` consumes the segment iterable exactly once: faster-whisper decodes lazily,
      so iterating it is what runs the model, and it must happen on the worker thread that
      called `transcribe`, never on the event loop.
    - A caller's language hint always wins over the language the model reports; a reported value
      that is not a lowercase ISO 639 code becomes None rather than a guess.

See Also:
    - .claude/codingrules.md section 8.6 for "mapping.py is the only file where vendor field
      names appear".
    - hivemind.llm.providers.whisper.provider for the adapter that calls this module.
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterable
from typing import BinaryIO, Protocol

from hivemind.llm.providers.whisper.config import WhisperConfig
from hivemind.llm.transcription import LANGUAGE_PATTERN, Transcript, TranscriptSegment

GPU_DEVICE = "cuda"  # The library's name for an NVIDIA GPU, the one accelerator it supports.
CPU_DEVICE = "cpu"  # The library's name for the CPU.
GPU_COMPUTE_TYPE = "float16"  # Half precision: the library's own recommendation on a GPU.
CPU_COMPUTE_TYPE = "int8"  # 8-bit weights on a CPU (ADR-0033): smaller and faster than float32.
_LANGUAGE_RE = re.compile(LANGUAGE_PATTERN)  # Screens a reported language before it is kept.

__all__ = [
    "CPU_COMPUTE_TYPE",
    "CPU_DEVICE",
    "GPU_COMPUTE_TYPE",
    "GPU_DEVICE",
    "InfoLike",
    "SegmentLike",
    "WhisperModelLike",
    "default_compute_type",
    "model_options",
    "to_transcript",
]


class SegmentLike(Protocol):
    """The fields of one faster-whisper segment this adapter reads."""

    @property
    def start(self) -> float:
        """Return the segment's start, in seconds from the clip's start."""
        ...

    @property
    def end(self) -> float:
        """Return the segment's end, in seconds from the clip's start."""
        ...

    @property
    def text(self) -> str:
        """Return the segment's text, usually with a leading space."""
        ...

    @property
    def avg_logprob(self) -> float:
        """Return the mean log-probability of the segment's tokens (0 or below)."""
        ...


class InfoLike(Protocol):
    """The field of faster-whisper's transcription info this adapter reads."""

    @property
    def language(self) -> str:
        """Return the language the model used: the hint given, or the one it detected."""
        ...


class WhisperModelLike(Protocol):
    """The one method of a loaded faster-whisper model this adapter calls."""

    def transcribe(
        self, audio: BinaryIO, language: str | None
    ) -> tuple[Iterable[SegmentLike], InfoLike]:
        """Start transcribing `audio`; the returned segments decode lazily as they are read.

        Args:
            audio: A readable WAV file object.
            language: An ISO 639 hint, or None to detect the language.

        Returns:
            The lazy segment iterable and the run's info.
        """
        ...


def default_compute_type(device: str) -> str:
    """Return the precision ADR-0033 picks for `device`: half on a GPU, 8-bit ints on a CPU.

    Args:
        device: `GPU_DEVICE` or `CPU_DEVICE`.

    Returns:
        `GPU_COMPUTE_TYPE` for the GPU, `CPU_COMPUTE_TYPE` otherwise.
    """
    return GPU_COMPUTE_TYPE if device == GPU_DEVICE else CPU_COMPUTE_TYPE


def model_options(config: WhisperConfig, device: str, compute_type: str) -> dict[str, object]:
    """Build the keyword options faster-whisper's model constructor takes, from `config`.

    Args:
        config: The provider's validated configuration.
        device: The resolved device (`GPU_DEVICE` or `CPU_DEVICE`), never "auto".
        compute_type: The resolved precision.

    Returns:
        The constructor's keyword arguments, by the library's own parameter names.
    """
    return {
        "device": device,
        "compute_type": compute_type,
        "cpu_threads": config.cpu_threads,
        "download_root": str(config.download_root) if config.download_root is not None else None,
        "local_files_only": config.local_files_only,
    }


def to_transcript(
    segments: Iterable[SegmentLike],
    info: InfoLike,
    duration_s: float,
    language: str | None,
) -> Transcript:
    """Convert one faster-whisper result into a Transcript, running the lazy decode as it goes.

    Args:
        segments: The library's lazy segment iterable; consumed exactly once, here.
        info: The library's transcription info.
        duration_s: The clip's own duration, which the transcript reports.
        language: The caller's hint, which wins over the reported language when given.

    Returns:
        A Transcript whose segments are trimmed, time-ordered and clamped non-negative.
    """
    ours = tuple(_segment(segment) for segment in segments)
    reported = info.language if _LANGUAGE_RE.match(info.language) else None
    return Transcript.from_segments(
        ours, language=language if language is not None else reported, duration_s=duration_s
    )


def _segment(segment: SegmentLike) -> TranscriptSegment:
    """Convert one library segment, clamping a negative start and an end before the start."""
    start_s = max(segment.start, 0.0)
    return TranscriptSegment(
        start_s=start_s,
        end_s=max(segment.end, start_s),
        text=segment.text.strip(),
        confidence=_confidence(segment.avg_logprob),
    )


def _confidence(avg_logprob: float) -> float | None:
    """Turn a mean token log-probability into a 0-to-1 confidence: its geometric-mean probability.

    A log-probability is never above zero, so it is capped there before exponentiating (a
    malformed positive value then reads as certainty rather than overflowing); a NaN, which no
    probability can be derived from, becomes None, "no figure", rather than a made-up number.
    """
    if math.isnan(avg_logprob):
        return None
    return math.exp(min(avg_logprob, 0.0))
