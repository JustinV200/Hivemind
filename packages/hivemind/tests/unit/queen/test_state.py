"""Tests for hivemind.queen.state: QueenMode, its transition table, and ClusterState.

Fits into the Hive:
    Mirrors src/hivemind/queen/state.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.queen.state for the module under test.
    - .claude/codingrules.md Appendix C, "Queen mode" row, for the transition table asserted here.
"""

from __future__ import annotations

import pytest

from hivemind.queen.state import (
    TRANSITIONS,
    ClusterState,
    InvalidQueenModeTransitionError,
    QueenMode,
    assert_transition,
    can_transition,
    is_terminal,
)

# Every allowed edge, read literally off Appendix C's "Queen mode" row, expanded (this module's
# own docstring): REQUEENING -> RUNNING; RUNNING <-> CLUSTERED; CLUSTERED -> SUPERSEDING ->
# SUPERSEDED; SUPERSEDING -> CLUSTERED (rollback).
_ALLOWED_EDGES: tuple[tuple[QueenMode, QueenMode], ...] = (
    (QueenMode.REQUEENING, QueenMode.RUNNING),
    (QueenMode.RUNNING, QueenMode.CLUSTERED),
    (QueenMode.CLUSTERED, QueenMode.RUNNING),
    (QueenMode.CLUSTERED, QueenMode.SUPERSEDING),
    (QueenMode.SUPERSEDING, QueenMode.SUPERSEDED),
    (QueenMode.SUPERSEDING, QueenMode.CLUSTERED),
)


def test_transitions_has_exactly_one_entry_per_mode() -> None:
    assert set(TRANSITIONS.keys()) == set(QueenMode)


@pytest.mark.parametrize(("from_mode", "to_mode"), _ALLOWED_EDGES)
def test_every_allowed_edge_can_transition(from_mode: QueenMode, to_mode: QueenMode) -> None:
    assert can_transition(from_mode, to_mode)
    assert_transition(from_mode, to_mode)  # Must not raise.


@pytest.mark.parametrize(
    ("from_mode", "to_mode"),
    [
        (from_mode, to_mode)
        for from_mode in QueenMode
        for to_mode in QueenMode
        if (from_mode, to_mode) not in _ALLOWED_EDGES
    ],
)
def test_every_other_pair_is_forbidden(from_mode: QueenMode, to_mode: QueenMode) -> None:
    assert not can_transition(from_mode, to_mode)
    with pytest.raises(InvalidQueenModeTransitionError):
        assert_transition(from_mode, to_mode)


def test_superseded_is_the_one_terminal_mode() -> None:
    assert is_terminal(QueenMode.SUPERSEDED)
    for mode in QueenMode:
        if mode is not QueenMode.SUPERSEDED:
            assert not is_terminal(mode)


def test_cluster_state_starts_running_with_no_clustered_providers() -> None:
    state = ClusterState()

    assert state.mode is QueenMode.RUNNING
    assert state.clustered_providers == frozenset()


def test_mark_clustered_moves_to_clustered_and_tracks_the_provider() -> None:
    state = ClusterState()

    state.mark_clustered("anthropic")

    assert state.mode is QueenMode.CLUSTERED
    assert state.clustered_providers == frozenset({"anthropic"})


def test_mark_clustered_twice_for_the_same_provider_is_idempotent() -> None:
    state = ClusterState()
    state.mark_clustered("anthropic")

    state.mark_clustered("anthropic")

    assert state.clustered_providers == frozenset({"anthropic"})


def test_mode_stays_clustered_while_any_provider_remains() -> None:
    state = ClusterState()
    state.mark_clustered("anthropic")
    state.mark_clustered("local")

    state.mark_resumed("anthropic")

    assert state.mode is QueenMode.CLUSTERED
    assert state.clustered_providers == frozenset({"local"})


def test_mode_returns_to_running_once_every_provider_resumes() -> None:
    state = ClusterState()
    state.mark_clustered("anthropic")
    state.mark_clustered("local")

    state.mark_resumed("anthropic")
    state.mark_resumed("local")

    assert state.mode is QueenMode.RUNNING
    assert state.clustered_providers == frozenset()


def test_mark_resumed_for_a_never_clustered_provider_is_a_no_op() -> None:
    state = ClusterState()

    state.mark_resumed("anthropic")

    assert state.mode is QueenMode.RUNNING
