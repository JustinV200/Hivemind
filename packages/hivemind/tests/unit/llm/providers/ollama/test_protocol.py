"""Tests for hivemind.llm.providers.ollama.protocol: the pull-progress value.

Fits into the Hive:
    Mirrors src/hivemind/llm/providers/ollama/protocol.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.llm.providers.ollama.protocol for the module under test.
"""

from __future__ import annotations

import pytest

from hivemind.llm.providers.ollama.protocol import PullProgress


def test_pull_progress_fraction_is_completed_over_total() -> None:
    progress = PullProgress(status="pulling x", digest="x", total=200, completed=50)

    assert progress.fraction == pytest.approx(0.25)


@pytest.mark.parametrize(("total", "completed"), [(None, 5), (5, None), (0, 0), (None, None)])
def test_pull_progress_fraction_is_none_without_usable_byte_counts(
    total: int | None, completed: int | None
) -> None:
    assert PullProgress("s", None, total, completed).fraction is None


def test_pull_progress_fraction_is_capped_at_one() -> None:
    assert PullProgress("s", "d", 10, 15).fraction == 1.0
