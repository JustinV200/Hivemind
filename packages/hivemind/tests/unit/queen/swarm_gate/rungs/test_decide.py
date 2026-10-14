"""Tests for hivemind.queen.swarm_gate.rungs.decide: the Queen's own decision to move a device.

`decide_rung` is a pure function of one value, so every case is a row: a reason to climb (the Hive
Stand's relief pressure, a task that must survive a lost link, the seat pressure) and the device
that can or cannot carry the rung; the operator's pin, which the Queen never climbs past and lowers
to; the marks that say which rungs she raised herself; the wait before she takes one back and the
idleness it needs; and the back-off after a failed move. Time is a fact handed in, so hysteresis is
a `timedelta` added to a mark, never a sleep.

Fits into the Hive:
    Mirrors src/hivemind/queen/swarm_gate/rungs/decide.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.queen.swarm_gate.rungs.decide for the module under test.
    - docs/adr/0052-runtime-ladder-warden-moves-first.md for the decisions' rules.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import timedelta

import pytest
from builders.cells import make_capabilities
from builders.forage import make_capacity
from builders.swarm import NOW, gpu_capacity, make_rung_facts, make_swarm_node, nuc_terms
from builders.swarm.gate import gate_terms

from hivemind.cell import AccessLevel
from hivemind.queen.swarm_gate.colonize import Step
from hivemind.queen.swarm_gate.rungs import Memory, Verdict, decide_rung, target_rung
from hivemind.swarm import RuntimeLevel, SwarmNode

G, C, N = RuntimeLevel.GATEWAY, RuntimeLevel.COLONIZED, RuntimeLevel.NUC
_LOWER_AFTER = timedelta(seconds=900)  # `[swarm.ladder] lower_after_s` in these terms.


def _device(level: RuntimeLevel = G, /, **fields: object) -> SwarmNode:
    """A device on `level` that can carry a Warden and, with `box` declared, a model server."""
    return make_swarm_node(None, None, **{"level": level, "capacity": gpu_capacity(16.0), **fields})


def _verdict(
    level: RuntimeLevel = G, node_fields: Mapping[str, object] | None = None, /, **facts: object
) -> Verdict:
    """Decide for a device on `level`, with the facts given and the Nuc terms and `box` declared."""
    node = _device(level, **(node_fields or {}))
    defaults: dict[str, object] = {"terms": nuc_terms(), "declared": ("box",)}
    return decide_rung(make_rung_facts(node, **{**defaults, **facts}))


def _ask(verdict: Verdict) -> tuple[Step | None, str | None]:
    """The step and the reason a verdict asks for; (None, None) when it asks for nothing."""
    ask = verdict.ask
    return (None, None) if ask is None else (ask.step, ask.word)


def _marked(level: RuntimeLevel, ago: timedelta) -> Memory:
    """What she remembers when she raised the device to `level` and last saw a reason `ago`."""
    return Memory(seen={level: NOW - ago})


# ─── Climbing the first rung: the Hive Stand's relief and a task's need ───────────────────────


@pytest.mark.parametrize(
    ("facts", "expected"),
    [
        ({"relief": 0.8}, (Step.PROMOTE, "relief")),  # At the threshold counts.
        ({"relief": 0.95}, (Step.PROMOTE, "relief")),
        ({"relief": 0.79}, (None, None)),  # Just under is not a reason.
        ({"survives": True}, (Step.PROMOTE, "outage")),
        ({"relief": 0.9, "survives": True}, (Step.PROMOTE, "relief")),  # The first that holds.
        ({}, (None, None)),  # Nothing asks.
    ],
)
def test_a_level_0_device_is_colonized_for_relief_or_for_a_task_that_must_survive(
    facts: dict[str, object], expected: tuple[Step | None, str | None]
) -> None:
    assert _ask(_verdict(G, **facts)) == expected


@pytest.mark.parametrize(
    ("node_fields", "word"),
    [
        ({"capabilities": make_capabilities(python_version="3.11.9")}, "python_too_old"),
        ({"capabilities": make_capabilities(python_version=None)}, "python_unknown"),
        ({"access_level": AccessLevel.READ_ONLY}, "access_too_low"),
        ({"capacity": make_capacity(max_sub_bees=0)}, "forage_too_small"),
    ],
)
def test_a_reason_that_holds_for_a_device_that_cannot_carry_a_warden_is_a_refusal_word(
    node_fields: dict[str, object], word: str
) -> None:
    verdict = _verdict(G, node_fields, survives=True)

    assert verdict == Verdict(declined=word)  # Nothing is started; the word is recorded once.


def test_a_device_nothing_asks_for_is_not_refused_whatever_it_lacks() -> None:
    node_fields = {"capabilities": make_capabilities(python_version="3.11.9")}

    assert _verdict(G, node_fields) == Verdict()  # No reason: no refusal to record.


# ─── Climbing the second rung: the seats and a task's autonomy ────────────────────────────────


@pytest.mark.parametrize(
    ("facts", "expected"),
    [
        ({"seats": 0.75}, (Step.NUC_PROMOTE, "pressure")),
        ({"seats": 0.74}, (None, None)),
        ({"survives": True}, (Step.NUC_PROMOTE, "autonomy")),
        ({"seats": 1.0, "survives": True}, (Step.NUC_PROMOTE, "pressure")),
        ({"relief": 1.0}, (None, None)),  # The Hive Stand's load asks for a Warden, not a server.
    ],
)
def test_a_colonized_device_gets_a_model_server_for_the_seats_or_a_tasks_autonomy(
    facts: dict[str, object], expected: tuple[Step | None, str | None]
) -> None:
    assert _ask(_verdict(C, **facts)) == expected


def test_the_nuc_step_carries_the_accepted_decision_the_flow_acts_on() -> None:
    verdict = _verdict(C, survives=True)

    assert verdict.ask is not None and verdict.ask.decision is not None
    assert verdict.ask.decision.server_id == "box"
    assert verdict.ask.decision.initial == ("small", "large")


@pytest.mark.parametrize(
    ("facts", "word"),
    [
        ({"declared": ()}, "no_server"),  # Its Warden declared no server.
        ({"declared": ("elsewhere",)}, "no_allowlist"),  # One the Hive lists no models for.
    ],
)
def test_a_reason_for_a_colonized_device_that_cannot_run_a_server_is_a_refusal_word(
    facts: dict[str, object], word: str
) -> None:
    assert _verdict(C, survives=True, **facts) == Verdict(declined=word)


def test_a_device_with_too_little_vram_for_any_model_is_a_refusal_word() -> None:
    small = {"capacity": gpu_capacity(2.0)}

    assert _verdict(C, small, survives=True) == Verdict(declined="vram_too_small")


def test_a_nuc_has_no_rung_above_it_to_climb() -> None:
    assert _verdict(N, relief=1.0, seats=1.0, survives=True).ask is None


# ─── The operator's pin ───────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("level", "ceiling", "facts"),
    [
        (G, G, {"relief": 1.0}),  # Pinned as a gateway: never colonized by her.
        (G, G, {"survives": True}),
        (C, C, {"seats": 1.0}),  # Demoted to colonized: never given a server by her.
        (C, C, {"survives": True}),
        (C, G, {"relief": 1.0, "idle": False}),  # Below the rung it is on: not climbed either.
    ],
)
def test_the_queen_never_climbs_past_the_operators_ceiling(
    level: RuntimeLevel, ceiling: RuntimeLevel, facts: dict[str, object]
) -> None:
    verdict = _verdict(level, {"ceiling": ceiling}, **facts)

    assert verdict.ask is None or verdict.ask.word == "pinned"  # Only ever down, never up.
    assert verdict.declined is None  # And a pin is silent: nothing was refused.


def test_a_ceiling_at_the_rung_above_still_lets_her_climb_to_it() -> None:
    assert _ask(_verdict(G, {"ceiling": C}, relief=0.9)) == (Step.PROMOTE, "relief")
    assert _ask(_verdict(C, {"ceiling": N}, seats=0.9)) == (Step.NUC_PROMOTE, "pressure")


@pytest.mark.parametrize(
    ("level", "ceiling", "step"),
    [
        (C, G, Step.WITHDRAW),
        (N, C, Step.NUC_DEMOTE),
        (N, G, Step.NUC_DEMOTE),  # One rung at a time: the Nuc comes down to colonized first.
    ],
)
def test_a_device_above_the_operators_ceiling_is_taken_down_a_rung_when_it_is_idle(
    level: RuntimeLevel, ceiling: RuntimeLevel, step: Step
) -> None:
    verdict = _verdict(level, {"ceiling": ceiling}, idle=True)

    assert _ask(verdict) == (step, "pinned")


def test_a_pin_waits_for_the_device_to_be_idle_but_not_for_any_reason_to_be_gone() -> None:
    busy = _verdict(C, {"ceiling": G}, idle=False, relief=1.0)
    idle = _verdict(C, {"ceiling": G}, idle=True, relief=1.0)  # A reason holds: the pin wins.

    assert busy.ask is None
    assert _ask(idle) == (Step.WITHDRAW, "pinned")


# ─── Keeping a rung she raised, and taking it back ────────────────────────────────────────────


def test_a_rung_she_raised_is_kept_while_a_reason_holds_and_its_mark_is_refreshed() -> None:
    memory = _marked(C, timedelta(hours=5))  # Long past `lower_after_s`: only the reason saves it.

    verdict = _verdict(
        C, memory=memory, idle=True, relief=0.8
    )  # The Hive Stand would still need it.

    assert verdict == Verdict(seen=C)


def test_the_mark_is_refreshed_even_on_the_pass_that_climbs_on() -> None:
    memory = _marked(C, timedelta(hours=5))

    verdict = _verdict(C, memory=memory, idle=True, survives=True)  # Also worth a model server.

    assert _ask(verdict) == (Step.NUC_PROMOTE, "autonomy") and verdict.seen is C


def test_a_nuc_she_raised_is_kept_while_the_seats_stay_held() -> None:
    memory = _marked(N, timedelta(hours=5))

    verdict = _verdict(N, memory=memory, idle=True, seats=0.9)

    assert verdict == Verdict(seen=N)


@pytest.mark.parametrize(
    ("ago", "idle", "step"),
    [
        (timedelta(seconds=901), True, Step.WITHDRAW),  # Gone long enough and idle.
        (timedelta(seconds=900), True, Step.WITHDRAW),  # Exactly the wait counts.
        (timedelta(seconds=899), True, None),  # One second short.
        (timedelta(hours=9), False, None),  # Long enough, but it is working.
        (timedelta(seconds=10), True, None),  # A lull between two tasks.
    ],
)
def test_a_rung_is_taken_back_only_after_its_reasons_have_been_gone_the_whole_wait_and_idle(
    ago: timedelta, idle: bool, step: Step | None
) -> None:
    verdict = _verdict(C, memory=_marked(C, ago), idle=idle)

    assert _ask(verdict) == (step, "reasons_gone" if step is not None else None)
    assert verdict.seen is None  # No reason held, so there is nothing to refresh.


def test_a_reason_that_holds_again_restarts_the_wait_instead_of_ending_it() -> None:
    gone = _marked(C, timedelta(hours=1))
    back = _verdict(C, memory=gone, idle=True, relief=0.9)

    assert back == Verdict(seen=C)  # And the caller refreshes the mark to now.
    refreshed = _marked(C, timedelta(seconds=0))
    assert _verdict(C, memory=refreshed, idle=True).ask is None  # So the wait starts over.


def test_a_nuc_is_taken_back_to_colonized_after_its_own_wait() -> None:
    terms = nuc_terms(nuc_lower_after_s=60.0, lower_after_s=3600.0)
    waited = _verdict(N, terms=terms, memory=_marked(N, timedelta(seconds=61)), idle=True)
    too_soon = _verdict(N, terms=terms, memory=_marked(N, timedelta(seconds=59)), idle=True)

    assert _ask(waited) == (Step.NUC_DEMOTE, "reasons_gone")
    assert too_soon.ask is None


def test_a_rung_nobody_recorded_her_raising_is_never_taken_back_by_the_wait() -> None:
    for level in (C, N):
        verdict = _verdict(level, memory=Memory(), idle=True)

        assert verdict.ask is None  # The operator raised it: it stays until the operator says.


def test_a_mark_for_another_rung_does_not_make_this_one_hers() -> None:
    verdict = _verdict(N, memory=_marked(C, timedelta(hours=9)), idle=True)

    assert verdict.ask is None


def test_a_device_on_the_first_rung_has_nothing_to_take_back() -> None:
    verdict = _verdict(G, memory=_marked(C, timedelta(hours=9)), idle=True)

    assert verdict == Verdict()


# ─── Back-off after a failed move ─────────────────────────────────────────────────────────────


def test_a_failed_climb_is_not_tried_again_until_its_wait_has_run_out() -> None:
    retry = Memory(retry_at={(G, C): NOW + timedelta(seconds=300)})

    waiting = _verdict(G, memory=retry, relief=0.9)
    later = _verdict(G, memory=retry, relief=0.9, now=NOW + timedelta(seconds=300))

    assert waiting == Verdict()
    assert _ask(later) == (Step.PROMOTE, "relief")


def test_a_failed_move_does_not_block_a_different_one() -> None:
    retry = Memory(retry_at={(C, N): NOW + timedelta(hours=1)})

    assert _ask(_verdict(G, memory=retry, relief=0.9)) == (Step.PROMOTE, "relief")


def test_a_failed_withdrawal_is_not_tried_again_until_its_wait_has_run_out() -> None:
    memory = Memory(
        seen={C: NOW - timedelta(hours=1)}, retry_at={(C, G): NOW + timedelta(seconds=60)}
    )

    assert _verdict(C, memory=memory, idle=True).ask is None
    later = _verdict(C, memory=memory, idle=True, now=NOW + timedelta(seconds=61))
    assert _ask(later) == (Step.WITHDRAW, "reasons_gone")


def test_a_pin_that_is_backing_off_waits_too() -> None:
    memory = Memory(retry_at={(C, G): NOW + timedelta(seconds=60)})

    assert _verdict(C, {"ceiling": G}, memory=memory, idle=True).ask is None


def test_a_refusal_is_reported_whether_or_not_the_move_is_backing_off() -> None:
    retry = Memory(retry_at={(G, C): NOW + timedelta(seconds=300)})
    unable = {"capabilities": make_capabilities(python_version="3.11.9")}

    assert _verdict(G, unable, memory=retry, survives=True) == Verdict(declined="python_too_old")


# ─── Which step lands on which rung ───────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("step", "rung"),
    [
        (Step.PROMOTE, C),
        (Step.NUC_PROMOTE, N),
        (Step.WITHDRAW, G),
        (Step.NUC_DEMOTE, C),
    ],
)
def test_each_step_lands_on_one_rung(step: Step, rung: RuntimeLevel) -> None:
    assert target_rung(step) is rung


def test_the_same_facts_give_the_same_verdict() -> None:
    facts = make_rung_facts(_device(C), terms=gate_terms(), relief=0.9, idle=True)

    assert decide_rung(facts) == decide_rung(facts)
