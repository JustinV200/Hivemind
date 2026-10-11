"""Grade the TRANSCRIBER slot by word error rate and latency over a directory of fixture clips.

Roadmap step 8.5: "`TRANSCRIBER` is graded by word error rate and latency on a fixture clip set,
so the default Whisper size is chosen from measurements." A **fixture set** is a directory of
`<clip>.wav` files, each beside a `<clip>.txt` holding exactly what was said. `wer` is the standard
word error rate: the minimum number of word substitutions, insertions and deletions that turn the
hypothesis into the reference, over the reference's length. `grade_clips` transcribes every clip
through the slot's resolved `BoundTranscriber` and scores each clip's WER, its latency and its
real-time factor (seconds spent per second of audio), then means them. The set the repository
ships is synthetic (generated tones, graded against a fake transcriber so the grader itself is
proven); real clips of real speech are the operator's to supply, and run once per candidate model
to pick the smallest one that clears the bar.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside `hivemind.cli.llm.eval`. Called by the
    evaluation pipeline when `--fixtures` is given. Calls into `hivemind.llm.transcription` (the
    bound transcriber, the gate, AudioClip) and `waggle.clock` only.

Key invariants:
    - `wer` is pure and total: it never divides by zero and never raises. Identical text, however
      it is cased or punctuated, is exactly 0.0; any non-empty reference against an empty
      hypothesis is exactly 1.0; an empty reference scores 0.0 against an empty hypothesis and
      1.0 against anything else.
    - Words are compared after lower-casing and dropping punctuation other than apostrophes, so
      the grade measures what was heard, not how a model happens to format it.
    - A clip without a reference, or a directory with no pairs, is a `FixtureSetError`: a grade
      over nothing would read as a perfect score.

See Also:
    - hivemind.llm.transcription for the slot's provider protocol and the fake used to test this.
    - docs/evals/README.md for how to run it on real clips.
"""

from __future__ import annotations

import re
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from hivemind.llm.transcription import AudioClip, BoundTranscriber, DirectTranscriptionGate
from waggle.clock import Clock

# Anything that is not a letter, digit, whitespace or an apostrophe is dropped before comparing.
_PUNCTUATION = re.compile(r"[^\w\s']", re.UNICODE)

__all__ = ["ClipScore", "FixtureSetError", "TranscriberGrade", "grade_clips", "wer"]


class FixtureSetError(ValueError):
    """Raise when a fixture directory holds no usable `<clip>.wav` + `<clip>.txt` pairs."""


class ClipScore(BaseModel):
    """One clip's score: how wrong the transcript was, and how long it took."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    clip: str = Field(description="The clip's file stem.")
    wer: float = Field(ge=0, description="Word error rate against the reference text.")
    latency_s: float = Field(ge=0, description="Seconds the transcription took.")
    duration_s: float = Field(ge=0, description="Seconds of audio in the clip.")
    real_time_factor: float | None = Field(
        description="Latency over duration: below 1.0 is faster than real time; None for silence."
    )


class TranscriberGrade(BaseModel):
    """The TRANSCRIBER slot's grade over a whole fixture set."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    provider: str = Field(description="The provider name the clips were transcribed on.")
    model: str = Field(description="The model id the clips were transcribed on.")
    clips: tuple[ClipScore, ...] = Field(description="Every clip's own score.")
    mean_wer: float = Field(ge=0, description="Mean word error rate over the clips.")
    mean_latency_s: float = Field(ge=0, description="Mean seconds per clip.")
    score: float = Field(ge=0, le=1, description="1 minus the mean WER, floored at 0.")


def wer(reference: str, hypothesis: str) -> float:
    """Return the word error rate of `hypothesis` against `reference`.

    Args:
        reference: What was actually said.
        hypothesis: What the transcriber heard.

    Returns:
        Word-level edit distance over the reference's word count; 0.0 for identical words. An
        empty reference scores 0.0 against an empty hypothesis and 1.0 against anything else,
        because the rate has no denominator and any word is entirely wrong.
    """
    expected, heard = _words(reference), _words(hypothesis)
    if not expected:
        return 0.0 if not heard else 1.0
    return _edit_distance(expected, heard) / len(expected)


async def grade_clips(
    bound: BoundTranscriber, fixtures: Path, clock: Clock, language: str | None = None
) -> TranscriberGrade:
    """Transcribe every clip in `fixtures` on `bound` and score WER and latency.

    Args:
        bound: The TRANSCRIBER slot's resolved chain; its first link's provider is used, through
            the unmetered direct gate, because this measures the model and not the Fanner.
        fixtures: A directory of `<clip>.wav` + `<clip>.txt` pairs.
        clock: Injected clock the latency is measured on.
        language: A lowercase ISO 639 hint to pass on every clip, or None to let the model detect.

    Returns:
        The grade; its `clips` are in file-name order.

    Raises:
        FixtureSetError: The directory is missing, holds no pairs, or a clip lacks its reference.
    """
    gate = DirectTranscriptionGate()
    scores: list[ClipScore] = []
    for wav in _clip_files(fixtures):
        reference = wav.with_suffix(".txt").read_text(encoding="utf-8")
        clip = AudioClip.from_wav(wav.read_bytes())
        start = clock.monotonic()
        # External await: the model's own transcription time is what is being measured.
        transcript = await gate.transcribe(bound, clip, language)
        latency = clock.monotonic() - start
        scores.append(
            ClipScore(
                clip=wav.stem,
                wer=wer(reference, transcript.text),
                latency_s=latency,
                duration_s=clip.duration_s,
                real_time_factor=latency / clip.duration_s if clip.duration_s > 0 else None,
            )
        )
    mean_wer = sum(score.wer for score in scores) / len(scores)
    return TranscriberGrade(
        provider=bound.provider.name,
        model=bound.model,
        clips=tuple(scores),
        mean_wer=mean_wer,
        mean_latency_s=sum(score.latency_s for score in scores) / len(scores),
        score=max(0.0, 1.0 - mean_wer),
    )


def _clip_files(fixtures: Path) -> list[Path]:
    """List the `.wav` files in `fixtures` in name order, each proven to have a reference."""
    if not fixtures.is_dir():
        raise FixtureSetError(f"fixture directory {fixtures} does not exist")
    clips = sorted(fixtures.glob("*.wav"))
    if not clips:
        raise FixtureSetError(f"no <clip>.wav files in {fixtures}")
    unreferenced = [clip.name for clip in clips if not clip.with_suffix(".txt").is_file()]
    if unreferenced:
        raise FixtureSetError(f"no <clip>.txt reference for: {', '.join(unreferenced)}")
    return clips


def _words(text: str) -> list[str]:
    """Split `text` into comparable words: lower-cased, punctuation (not apostrophes) removed."""
    return _PUNCTUATION.sub(" ", text.lower()).split()


def _edit_distance(expected: list[str], heard: list[str]) -> int:
    """Return the word-level Levenshtein distance, in O(len(expected) * len(heard)) time."""
    # One row of the classic dynamic-programming table at a time keeps memory linear.
    previous = list(range(len(heard) + 1))
    for i, expected_word in enumerate(expected, start=1):
        current = [i]
        for j, heard_word in enumerate(heard, start=1):
            substitution = previous[j - 1] + (expected_word != heard_word)
            current.append(min(previous[j] + 1, current[j - 1] + 1, substitution))
        previous = current
    return previous[-1]
