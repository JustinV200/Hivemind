"""Check the episode view: a record's taint crosses as its two enum names, nothing else.

The Thoughts view marks a tainted record, so the view carries the marker's state and source; the
marker's reason is free text another setter wrote and stays behind. The literal names the
document publishes must stay in step with the memory enums they mirror.
"""

from __future__ import annotations

from typing import get_args

from builders.memory import make_episode

from hivemind.memory import TaintMarker, TaintSource, TaintState
from hivemind.observation import episode_view
from hivemind.observation.views.episodes import TaintSourceName, TaintStateName
from waggle.clock import FakeClock
from waggle.ids import new_event_id


def test_unlabelled_record_has_no_taint() -> None:
    """A record nothing labelled shows null for both taint fields."""
    view = episode_view(make_episode())
    assert view.taint is None
    assert view.taint_source is None


def test_tainted_record_crosses_as_its_enum_names() -> None:
    """A tainted record's state and source cross; its reason does not."""
    clock = FakeClock()
    marker = TaintMarker(
        state=TaintState.TAINTED,
        source=TaintSource.QUARANTINE,
        reason="the scanner matched a pattern",
        event_id=new_event_id(clock),
        at=clock.now(),
    )
    view = episode_view(make_episode(clock, tainted=marker))
    assert view.taint == "tainted"
    assert view.taint_source == "quarantine"
    assert "scanner" not in view.model_dump_json()


def test_taint_names_match_the_memory_enums() -> None:
    """The published literals are exactly the enums' wire values (spec drift guard)."""
    assert set(get_args(TaintStateName)) == {state.value for state in TaintState}
    assert set(get_args(TaintSourceName)) == {source.value for source in TaintSource}
