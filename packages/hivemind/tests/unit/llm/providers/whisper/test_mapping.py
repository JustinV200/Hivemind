"""Tests for hivemind.llm.providers.whisper.mapping: faster-whisper results into Transcripts.

Fits into the Hive:
    Mirrors src/hivemind/llm/providers/whisper/mapping.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.llm.providers.whisper.mapping for the module under test.
"""

from __future__ import annotations

import math
from collections.abc import Iterator
from pathlib import Path

import pytest
from builders.audio import StandInInfo, StandInSegment

from hivemind.llm.providers.whisper.config import WhisperConfig
from hivemind.llm.providers.whisper.mapping import (
    CPU_COMPUTE_TYPE,
    CPU_DEVICE,
    GPU_COMPUTE_TYPE,
    GPU_DEVICE,
    default_compute_type,
    model_options,
    to_transcript,
)


def test_default_compute_type_is_half_precision_on_a_gpu_and_int8_on_a_cpu() -> None:
    assert default_compute_type(GPU_DEVICE) == GPU_COMPUTE_TYPE
    assert default_compute_type(CPU_DEVICE) == CPU_COMPUTE_TYPE


def test_model_options_carry_the_resolved_device_and_the_offline_switch() -> None:
    config = WhisperConfig(
        model="m", download_root=Path("weights"), local_files_only=True, cpu_threads=2
    )

    options = model_options(config, CPU_DEVICE, CPU_COMPUTE_TYPE)

    assert options == {
        "device": CPU_DEVICE,
        "compute_type": CPU_COMPUTE_TYPE,
        "cpu_threads": 2,
        "download_root": "weights",
        "local_files_only": True,
    }


def test_model_options_leave_the_download_root_to_the_library_when_unset() -> None:
    options = model_options(WhisperConfig(model="m"), GPU_DEVICE, GPU_COMPUTE_TYPE)

    assert options["download_root"] is None


def test_to_transcript_trims_orders_and_scores_every_segment() -> None:
    segments = [
        StandInSegment(start=0.5, end=0.9, text=" the hive.", avg_logprob=-0.5),
        StandInSegment(start=0.0, end=0.5, text=" Hello from", avg_logprob=-0.1),
    ]

    transcript = to_transcript(segments, StandInInfo("en"), 1.0, None)

    assert transcript.text == "Hello from the hive."
    assert [segment.text for segment in transcript.segments] == ["Hello from", "the hive."]
    assert transcript.segments[0].confidence == pytest.approx(math.exp(-0.1))
    assert transcript.language == "en"
    assert transcript.duration_s == 1.0


def test_to_transcript_clamps_a_negative_start_and_an_end_before_the_start() -> None:
    segments = [StandInSegment(start=-0.2, end=-0.1, text="edge")]

    transcript = to_transcript(segments, StandInInfo("en"), 1.0, None)

    assert (transcript.segments[0].start_s, transcript.segments[0].end_s) == (0.0, 0.0)


def test_to_transcript_prefers_the_callers_language_hint() -> None:
    transcript = to_transcript([], StandInInfo("en"), 1.0, "de")

    assert transcript.language == "de"


def test_to_transcript_drops_a_reported_language_that_is_not_a_code() -> None:
    transcript = to_transcript([], StandInInfo("English"), 1.0, None)

    assert transcript.language is None


@pytest.mark.parametrize(
    ("avg_logprob", "expected"), [(0.7, 1.0), (float("-inf"), 0.0), (float("nan"), None)]
)
def test_to_transcript_bounds_a_malformed_log_probability(
    avg_logprob: float, expected: float | None
) -> None:
    segments = [StandInSegment(start=0.0, end=0.1, text="x", avg_logprob=avg_logprob)]

    transcript = to_transcript(segments, StandInInfo("en"), 1.0, None)

    assert transcript.segments[0].confidence == expected


def test_to_transcript_consumes_the_lazy_segments_exactly_once() -> None:
    reads: list[int] = []

    def lazy() -> Iterator[StandInSegment]:
        # faster-whisper decodes as it is iterated; count how often that happens.
        reads.append(1)
        yield StandInSegment(start=0.0, end=0.2, text="once")

    transcript = to_transcript(lazy(), StandInInfo("en"), 1.0, None)

    assert transcript.text == "once"
    assert reads == [1]
