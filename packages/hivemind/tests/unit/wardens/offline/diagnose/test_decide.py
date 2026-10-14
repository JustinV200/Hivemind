"""Tests for hivemind.wardens.offline.diagnose.decide: the judgement behind a lost-link search.

Fits into the Hive:
    Mirrors src/hivemind/wardens/offline/diagnose/decide.py (codingrules section 3). Everything in
    the module is pure, so each judgement is a table: how a Drone's closing text is read for the
    cause it named, what the search does next from where it is (every phase against every fact
    that can move it), how a search is judged by the link and the cause, and which model a Drone
    can run on from the Warden's own pool.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.wardens.offline.diagnose.decide for the module under test.
    - docs/adr/0056-active-reconnection-diagnostic-drones.md for the rules these rows state.
"""

from __future__ import annotations

import pytest
from builders.forage import make_source

from hivemind.forage import Effort, ModelSource
from hivemind.forage.map import SlotBinding
from hivemind.wardens.offline import ReconnectBudget
from hivemind.wardens.offline.diagnose import (
    LINGER_S,
    VERIFY_S,
    Move,
    Phase,
    Snapshot,
    Verdict,
    decide_move,
    decide_verdict,
    local_binding,
    next_deadline_s,
    read_finding,
)

_BUDGET = ReconnectBudget(attempts=2, wall_clock_s=300.0)


@pytest.mark.parametrize(
    ("text", "cause"),
    [
        ("CAUSE: a stale resolver entry\nI removed it.", "a stale resolver entry"),
        ("cause: lower case counts", "lower case counts"),
        ("\n\n  CAUSE:   spaced out  \nrest", "spaced out"),
        ("CAUSE: none found\nThe route is there.", None),
        ("CAUSE: None.", None),
        ("CAUSE: unknown", None),
        ("CAUSE:", None),
        ("The cause is a stale resolver entry.", None),
        ("It is fine. CAUSE: not on the first line", None),
        ("", None),
    ],
)
def test_a_drones_closing_text_names_a_cause_only_on_its_first_line(
    text: str, cause: str | None
) -> None:
    finding = read_finding(text)

    assert finding.cause == cause
    assert finding.text == text  # Everything it wrote is kept.


def _snapshot(phase: Phase, **facts: object) -> Snapshot:
    """A snapshot in `phase` with nothing happening, `facts` changed."""
    fields: dict[str, object] = {
        "phase": phase,
        "attempts": 1,
        "budget": _BUDGET,
        "elapsed_s": 10.0,
        "link_up_for_s": None,
        "ended": False,
        "verifying_s": 0.0,
    }
    fields.update(facts)
    return Snapshot(**fields)  # type: ignore[arg-type]  # test-only field table


_PAST_BUDGET = {"elapsed_s": _BUDGET.wall_clock_s}
_ROWS: list[tuple[str, Snapshot, Move]] = [
    ("running, working", _snapshot(Phase.RUNNING), Move.WAIT),
    ("running, ended", _snapshot(Phase.RUNNING, ended=True), Move.COLLECT),
    ("running, out of time", _snapshot(Phase.RUNNING, **_PAST_BUDGET), Move.STOP),
    (
        "running, ended and out of time",
        _snapshot(Phase.RUNNING, ended=True, **_PAST_BUDGET),
        Move.COLLECT,
    ),
    ("running, link just back", _snapshot(Phase.RUNNING, link_up_for_s=LINGER_S - 1), Move.WAIT),
    ("running, link back a while", _snapshot(Phase.RUNNING, link_up_for_s=LINGER_S), Move.STOP),
    ("verifying, waiting", _snapshot(Phase.VERIFYING, verifying_s=VERIFY_S - 1), Move.WAIT),
    ("verifying, link back", _snapshot(Phase.VERIFYING, link_up_for_s=0.0), Move.FINISH),
    ("verifying, wait over", _snapshot(Phase.VERIFYING, verifying_s=VERIFY_S), Move.FINISH),
    ("verifying, out of time", _snapshot(Phase.VERIFYING, **_PAST_BUDGET), Move.FINISH),
    ("idle, attempts left", _snapshot(Phase.IDLE), Move.START),
    ("idle, none sent yet", _snapshot(Phase.IDLE, attempts=0), Move.START),
    ("idle, attempts spent", _snapshot(Phase.IDLE, attempts=2), Move.FINISH),
    ("idle, out of time", _snapshot(Phase.IDLE, **_PAST_BUDGET), Move.FINISH),
    ("idle, link back", _snapshot(Phase.IDLE, link_up_for_s=1.0), Move.FINISH),
    ("over", _snapshot(Phase.OVER, link_up_for_s=1.0, **_PAST_BUDGET), Move.WAIT),
]


@pytest.mark.parametrize(
    ("snapshot", "move"), [(r[1], r[2]) for r in _ROWS], ids=[r[0] for r in _ROWS]
)
def test_each_state_of_the_search_has_its_next_move(snapshot: Snapshot, move: Move) -> None:
    assert decide_move(snapshot) is move


_BUDGET_LEFT = _BUDGET.wall_clock_s - 10.0  # What `_snapshot` leaves of the budget by default.
_DEADLINES: list[tuple[str, Snapshot, float | None]] = [
    ("running, working: the budget", _snapshot(Phase.RUNNING), _BUDGET_LEFT),
    ("running, ended: collected now", _snapshot(Phase.RUNNING, ended=True), _BUDGET_LEFT),
    (
        "running, link back: the write-up ends first",
        _snapshot(Phase.RUNNING, link_up_for_s=LINGER_S - 5),
        5.0,
    ),
    (
        "running, link back late in the budget: the budget ends first",
        _snapshot(Phase.RUNNING, link_up_for_s=0.0, elapsed_s=_BUDGET.wall_clock_s - 1),
        1.0,
    ),
    (
        "running, out of time: acted on already",
        _snapshot(Phase.RUNNING, **_PAST_BUDGET, link_up_for_s=LINGER_S),
        None,
    ),
    (
        "verifying: the wait for the link ends first",
        _snapshot(Phase.VERIFYING, verifying_s=VERIFY_S - 7),
        7.0,
    ),
    (
        "verifying, late in the budget: the budget ends first",
        _snapshot(Phase.VERIFYING, elapsed_s=_BUDGET.wall_clock_s - 2),
        2.0,
    ),
    ("idle, no slot yet: the budget", _snapshot(Phase.IDLE), _BUDGET_LEFT),
    ("over: nothing", _snapshot(Phase.OVER), None),
]


@pytest.mark.parametrize(
    ("snapshot", "wait"), [(r[1], r[2]) for r in _DEADLINES], ids=[r[0] for r in _DEADLINES]
)
def test_the_clock_alone_ends_a_search_at_the_soonest_of_its_deadlines(
    snapshot: Snapshot, wait: float | None
) -> None:
    assert next_deadline_s(snapshot) == wait


@pytest.mark.parametrize("row", [r[1] for r in _ROWS], ids=[r[0] for r in _ROWS])
def test_a_deadline_is_never_one_that_has_already_passed(row: Snapshot) -> None:
    deadline = next_deadline_s(row)

    # A tick woken for a deadline in the past would find it acted on and wake itself again at once.
    assert deadline is None or deadline > 0


@pytest.mark.parametrize(
    ("link_up", "cause", "verdict", "reason"),
    [
        (True, "a stale entry", Verdict.FIXED, "link_back"),
        (True, None, Verdict.NO_CAUSE, "link_returned"),
        (False, "a stale entry", Verdict.DIAGNOSED, "cause_known"),
        (False, None, Verdict.NO_CAUSE, "no_cause"),
    ],
)
def test_a_search_is_judged_by_the_link_and_not_by_what_a_drone_claimed(
    link_up: bool, cause: str | None, verdict: Verdict, reason: str
) -> None:
    assert decide_verdict(link_up=link_up, cause=cause) == (verdict, reason)


def _row(key: str, provider: str, model: str, fallback: str | None = None) -> SlotBinding:
    """An `[llm.slots]` row."""
    return SlotBinding(
        key=key, provider=provider, model=model, fallback=fallback, effort=Effort.MEDIUM
    )


def _served(provider: str, model: str) -> ModelSource:
    """A source a server of the Warden's is serving."""
    return make_source(source_id=f"{provider}-{model}", provider=provider, model=model)


def test_a_drone_runs_on_the_first_worker_key_a_loaded_source_serves_from_this_machine() -> None:
    rows = (
        _row("queen", "hosted", "big", None),
        _row("worker", "hosted", "mid", "local_worker"),
        _row("local_worker", "box", "small", None),
    )

    found = local_binding(rows, [_served("box", "small")], lambda key: key == "local_worker")

    assert found is not None
    assert (found.key, found.source.source_id) == ("local_worker", "box-small")


def test_a_worker_row_that_is_served_and_local_is_taken_in_manifest_order() -> None:
    rows = (_row("worker", "box", "small", "second"), _row("second", "box", "small"))

    found = local_binding(rows, [_served("box", "small")], lambda key: True)

    assert found is not None and found.key == "worker"


@pytest.mark.parametrize(
    ("rows", "loaded", "is_local"),
    [
        # A model nothing is serving: the provider is configured and the server is not running.
        ((_row("worker", "box", "small"),), [], True),
        # The wrong model of a server that is running.
        ((_row("worker", "box", "large"),), [("box", "small")], True),
        # Served, but the chain reaches a hosted fallback, which a cut-off Warden must not use.
        ((_row("worker", "box", "small"),), [("box", "small")], False),
        # Served and local, but it serves another slot than a Drone's.
        ((_row("queen", "box", "small"),), [("box", "small")], True),
        # No rows at all.
        ((), [("box", "small")], True),
    ],
    ids=["nothing-served", "wrong-model", "hosted-fallback", "not-the-drones-slot", "no-rows"],
)
def test_a_warden_with_no_such_model_has_no_drone_to_send(
    rows: tuple[SlotBinding, ...], loaded: list[tuple[str, str]], is_local: bool
) -> None:
    sources = [_served(provider, model) for provider, model in loaded]

    assert local_binding(rows, sources, lambda key: is_local) is None
