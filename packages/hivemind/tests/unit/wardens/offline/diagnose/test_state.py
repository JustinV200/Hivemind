"""Tests for hivemind.wardens.offline.diagnose.state: the record of one outage's search.

Fits into the Hive:
    Mirrors src/hivemind/wardens/offline/diagnose/state.py (codingrules section 3). A search starts
    idle, with no Drone sent and no cause named; the cause it reports is the latest one a Drone
    named, and a later Drone that found nothing does not take it back, because a cause that was
    named is a thing the Hive should hear of whether or not the next attempt found another.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.wardens.offline.diagnose.state for the module under test.
"""

from __future__ import annotations

from datetime import UTC, datetime

from hivemind.wardens.offline.diagnose import Finding, Investigation, Phase

_CUT = datetime(2026, 1, 1, tzinfo=UTC)


def test_a_new_search_is_idle_with_nothing_sent_and_no_cause() -> None:
    search = Investigation(cut_since=_CUT)

    assert search.phase is Phase.IDLE and search.attempts == 0
    assert search.run is None and search.worker_id is None
    assert search.findings == [] and search.cause is None
    assert search.link_up_since is None and search.verifying_since is None
    assert search.starved is False


def test_the_cause_is_the_latest_one_a_drone_named() -> None:
    search = Investigation(
        cut_since=_CUT,
        findings=[Finding("the resolver", "a"), Finding(None, "b"), Finding("the route", "c")],
    )

    assert search.cause == "the route"


def test_a_cause_survives_a_later_drone_that_found_nothing() -> None:
    search = Investigation(
        cut_since=_CUT, findings=[Finding("the resolver", "a"), Finding(None, "b")]
    )

    assert search.cause == "the resolver"


def test_findings_are_not_shared_between_searches() -> None:
    first, second = Investigation(cut_since=_CUT), Investigation(cut_since=_CUT)

    first.findings.append(Finding("the resolver", "a"))

    assert second.findings == [] and second.cause is None
