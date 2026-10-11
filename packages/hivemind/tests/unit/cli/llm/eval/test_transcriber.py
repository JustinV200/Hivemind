"""Tests for hivemind.cli.llm.eval.transcriber: word error rate and the fixture-clip runner.

Fits into the Hive:
    Mirrors src/hivemind/cli/llm/eval/transcriber.py (codingrules section 3). The clips are
    generated tones (`builders.evals.write_fixture_clips`) graded through `FakeTranscription`, so
    the grader is proven without any real speech or model; real clips are the operator's.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.cli.llm.eval.transcriber for the module under test.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from builders.evals import write_fixture_clips
from hypothesis import given
from hypothesis import strategies as st

from hivemind.cli.llm.eval.transcriber import FixtureSetError, grade_clips, wer
from hivemind.forage import ModelSlot
from hivemind.llm.transcription import BoundTranscriber, FakeTranscription
from waggle.clock import FakeClock

_WORDS = st.text(alphabet="abcdefg", min_size=1, max_size=6)
_SENTENCE = st.lists(_WORDS, min_size=1, max_size=12).map(" ".join)


def _bound(*heard: str) -> BoundTranscriber:
    """A transcriber that hears `heard` in order, one entry per clip."""
    fake = FakeTranscription(name="scripted-ears")
    fake.script(*heard)
    return BoundTranscriber(ModelSlot.TRANSCRIBER, "transcriber", fake, "scripted-model")


# ──────────────────────────────────────────────────────────────────────────────
# wer
# ──────────────────────────────────────────────────────────────────────────────


@given(_SENTENCE)
def test_wer_of_identical_text_is_exactly_zero(text: str) -> None:
    assert wer(text, text) == 0.0


@given(_SENTENCE, _SENTENCE)
def test_wer_is_never_negative(reference: str, hypothesis: str) -> None:
    assert wer(reference, hypothesis) >= 0.0


@given(_SENTENCE)
def test_wer_of_an_empty_hypothesis_is_exactly_one(reference: str) -> None:
    assert wer(reference, "") == 1.0


@given(_SENTENCE, _SENTENCE)
def test_wer_is_at_most_the_longer_texts_length_over_the_reference(
    reference: str, hypothesis: str
) -> None:
    ref_words, hyp_words = len(reference.split()), len(hypothesis.split())

    assert wer(reference, hypothesis) <= max(ref_words, hyp_words) / ref_words


def test_wer_counts_substitutions_insertions_and_deletions() -> None:
    assert wer("the cat sat", "the dog sat") == pytest.approx(1 / 3)  # One substitution.
    assert wer("the cat sat", "the cat sat down") == pytest.approx(1 / 3)  # One insertion.
    assert wer("the cat sat", "the sat") == pytest.approx(1 / 3)  # One deletion.


def test_wer_ignores_case_and_punctuation_but_not_apostrophes() -> None:
    assert wer("Hello, world!", "hello world") == 0.0
    assert wer("don't stop", "dont stop") == pytest.approx(0.5)


def test_wer_of_an_empty_reference_is_defined_not_a_division_error() -> None:
    assert wer("", "") == 0.0
    assert wer("", "anything at all") == 1.0
    assert wer("   ", "") == 0.0


# ──────────────────────────────────────────────────────────────────────────────
# grade_clips
# ──────────────────────────────────────────────────────────────────────────────


async def test_grade_clips_scores_each_clip_and_means_them(tmp_path: Path) -> None:
    clips = write_fixture_clips(tmp_path, {"a_hello": "hello bee world", "b_count": "one two"})
    bound = _bound("hello bee world", "one too")  # The second clip has one of two words wrong.

    grade = await grade_clips(bound, clips, FakeClock())

    assert [clip.clip for clip in grade.clips] == ["a_hello", "b_count"]  # File-name order.
    assert [clip.wer for clip in grade.clips] == [0.0, 0.5]
    assert grade.mean_wer == 0.25
    assert grade.score == 0.75
    assert (grade.provider, grade.model) == ("scripted-ears", "scripted-model")


async def test_grade_clips_reports_each_clips_duration_and_a_real_time_factor(
    tmp_path: Path,
) -> None:
    clips = write_fixture_clips(tmp_path, {"only": "hi"})

    grade = await grade_clips(_bound("hi"), clips, FakeClock())

    clip = grade.clips[0]
    assert clip.duration_s == pytest.approx(0.25)
    assert clip.latency_s == 0.0  # A FakeClock does not move while a fake answers.
    assert clip.real_time_factor == 0.0


async def test_grade_clips_score_floors_at_zero_when_the_wer_exceeds_one(tmp_path: Path) -> None:
    clips = write_fixture_clips(tmp_path, {"only": "hi"})

    grade = await grade_clips(_bound("a great many extra words here"), clips, FakeClock())

    assert grade.mean_wer > 1.0
    assert grade.score == 0.0


async def test_grade_clips_refuses_a_missing_directory(tmp_path: Path) -> None:
    with pytest.raises(FixtureSetError, match="does not exist"):
        await grade_clips(_bound(), tmp_path / "nope", FakeClock())


async def test_grade_clips_refuses_a_directory_with_no_clips(tmp_path: Path) -> None:
    with pytest.raises(FixtureSetError, match=r"no <clip>\.wav"):
        await grade_clips(_bound(), tmp_path, FakeClock())


async def test_grade_clips_refuses_a_clip_with_no_reference_text(tmp_path: Path) -> None:
    clips = write_fixture_clips(tmp_path, {"lonely": "hi"})
    (clips / "lonely.txt").unlink()

    with pytest.raises(FixtureSetError, match=r"lonely\.wav"):
        await grade_clips(_bound("hi"), clips, FakeClock())
