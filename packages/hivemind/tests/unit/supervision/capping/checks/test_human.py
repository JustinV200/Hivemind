"""Unit tests for hivemind.supervision.capping.checks.human: HumanCheck.ask and .resolve."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest
from builders.capping import FakeAsker, make_proposal
from builders.cells import make_capabilities, make_cell

from hivemind.cell import AccessLevel, Cell, CellKind, OsFamily
from hivemind.cell.leavings import ApprovedBy
from hivemind.supervision.capping.checks.human import (
    DISCARD_OPTION,
    KEEP_FOR_GOAL_OPTION,
    KEEP_OPTION,
    HumanCheck,
)
from hivemind.supervision.capping.leave.model import LeaveHumanVerdict, LeaveVerdict, PathClass
from hivemind.supervision.capping.leave.persist import (
    LeaveDecisionRecord,
    LeavePersistDecision,
    build_leave_context,
    with_asker,
)
from hivemind.supervision.capping.leave.table import load_leave_policy
from hivemind.supervision.capping.tiers import RiskTier
from waggle.clock import FakeClock
from waggle.ids import new_message_id, new_task_id
from waggle.messages.labels import HoneyClearance as WireHoneyClearance
from waggle.messages.supervision import Answer, AnswerSource

_HOST_OS_FAMILY = OsFamily.WINDOWS if os.name == "nt" else OsFamily.LINUX
_TIMEOUT_S = 10.0


def _cell() -> Cell:
    return make_cell(
        kind=CellKind.REAL,
        source="hive_stand",
        access_level=AccessLevel.FULL,
        capabilities=make_capabilities(os=_HOST_OS_FAMILY),
    )


def _answer(*, chosen_option: int | None, source: AnswerSource = AnswerSource.HUMAN) -> Answer:
    clock = FakeClock()
    # A human's answer is always C2 (Answer's own validator); any other source may stay C1.
    clearance = WireHoneyClearance.C2 if source is AnswerSource.HUMAN else WireHoneyClearance.C1
    return Answer(
        question_id=new_message_id(clock),
        task_id=new_task_id(clock),
        text="okay",
        chosen_option=chosen_option,
        source=source,
        clearance=clearance,
    )


# ──────────────────────────────────────────────────────────────────────────────
# ask
# ──────────────────────────────────────────────────────────────────────────────


async def test_ask_raises_a_question_with_the_three_closed_options(tmp_path: Path) -> None:
    clock = FakeClock()
    asker = FakeAsker(answer=_answer(chosen_option=0))
    proposal = make_proposal(risk_tier=RiskTier.OUTSIDE_SCRATCH_WRITE)
    check = HumanCheck(clock, _TIMEOUT_S)

    await check.ask(asker, proposal, str(tmp_path / "keep.txt"), "the goal asked for it")

    assert len(asker.questions) == 1
    assert asker.questions[0].options == (KEEP_OPTION, KEEP_FOR_GOAL_OPTION, DISCARD_OPTION)
    assert asker.questions[0].task_id == proposal.task_id
    assert asker.questions[0].asked_by == proposal.proposer


@pytest.mark.parametrize(
    ("chosen_option", "expected"),
    [
        (0, LeaveHumanVerdict.KEEP),
        (1, LeaveHumanVerdict.KEEP_FOR_GOAL),
        (2, LeaveHumanVerdict.DISCARD),
    ],
)
async def test_ask_maps_each_option_index_to_its_own_verdict(
    chosen_option: int, expected: LeaveHumanVerdict, tmp_path: Path
) -> None:
    asker = FakeAsker(answer=_answer(chosen_option=chosen_option))
    proposal = make_proposal(risk_tier=RiskTier.OUTSIDE_SCRATCH_WRITE)
    check = HumanCheck(FakeClock(), _TIMEOUT_S)

    verdict = await check.ask(asker, proposal, str(tmp_path / "x"), "reason")

    assert verdict is expected


async def test_ask_discards_for_a_non_human_answer(tmp_path: Path) -> None:
    asker = FakeAsker(answer=_answer(chosen_option=0, source=AnswerSource.QUEEN))
    proposal = make_proposal(risk_tier=RiskTier.OUTSIDE_SCRATCH_WRITE)
    check = HumanCheck(FakeClock(), _TIMEOUT_S)

    verdict = await check.ask(asker, proposal, str(tmp_path / "x"), "reason")

    assert verdict is LeaveHumanVerdict.DISCARD


async def test_ask_discards_for_an_unanswered_question_past_its_timeout(tmp_path: Path) -> None:
    clock = FakeClock()
    asker = FakeAsker(hang=True)
    proposal = make_proposal(risk_tier=RiskTier.OUTSIDE_SCRATCH_WRITE)
    check = HumanCheck(clock, _TIMEOUT_S)

    task = asyncio.ensure_future(check.ask(asker, proposal, str(tmp_path / "x"), "reason"))
    # Several loop turns: ask_task and timeout_task are each their own nested asyncio.Task, and
    # each needs its own turn to reach its first real await (asker.ask's own hang point, and
    # clock.sleep's own registration into FakeClock's pending list) before advance() has anything
    # to resolve -- a single sleep(0) is not reliably enough (mirrors tests.unit.wardens.spawn.
    # test_spawn's own bounded polling loop for the same reason).
    for _ in range(10):
        await asyncio.sleep(0)
        if asker.questions:
            break
    clock.advance(_TIMEOUT_S)
    verdict = await task

    assert verdict is LeaveHumanVerdict.DISCARD
    assert len(asker.questions) == 1  # The question really was raised before timing out.


# ──────────────────────────────────────────────────────────────────────────────
# resolve
# ──────────────────────────────────────────────────────────────────────────────


def _ask_decision(verdict: LeaveVerdict, *, persisted: bool = False) -> LeavePersistDecision:
    record = LeaveDecisionRecord(
        path="/home/op/keep.txt",
        path_class=PathClass.HOME,
        verdict=verdict,
        persisted=persisted,
        reason="a declared leaving",
    )
    return LeavePersistDecision(persist=persisted, approved_by=None, reason=None, record=record)


async def test_resolve_is_a_noop_for_a_non_ask_verdict(tmp_path: Path) -> None:
    decision = _ask_decision(LeaveVerdict.ALLOW, persisted=True)
    leave = with_asker(
        build_leave_context(_cell(), load_leave_policy(), (), None, tmp_path),
        FakeAsker(answer=_answer(chosen_option=0)),
        _TIMEOUT_S,
        FakeClock(),
    )
    check = HumanCheck(FakeClock(), _TIMEOUT_S)
    proposal = make_proposal(risk_tier=RiskTier.OUTSIDE_SCRATCH_WRITE)

    resolved = await check.resolve(leave, decision, proposal)

    assert resolved is decision  # Untouched: resolve() never runs a HumanCheck for non-ASK.


async def test_resolve_is_a_noop_when_no_asker_is_wired(tmp_path: Path) -> None:
    decision = _ask_decision(LeaveVerdict.ASK)
    leave = build_leave_context(_cell(), load_leave_policy(), (), None, tmp_path)  # asker=None
    check = HumanCheck(FakeClock(), _TIMEOUT_S)
    proposal = make_proposal(risk_tier=RiskTier.OUTSIDE_SCRATCH_WRITE)

    resolved = await check.resolve(leave, decision, proposal)

    assert resolved is decision


async def test_resolve_keep_persists_with_human_approval(tmp_path: Path) -> None:
    decision = _ask_decision(LeaveVerdict.ASK)
    asker = FakeAsker(answer=_answer(chosen_option=0))
    leave = with_asker(
        build_leave_context(_cell(), load_leave_policy(), (), None, tmp_path),
        asker,
        _TIMEOUT_S,
        FakeClock(),
    )
    check = HumanCheck(FakeClock(), _TIMEOUT_S)
    proposal = make_proposal(risk_tier=RiskTier.OUTSIDE_SCRATCH_WRITE)

    resolved = await check.resolve(leave, decision, proposal)

    assert resolved.persist is True
    assert resolved.approved_by is ApprovedBy.HUMAN
    assert resolved.record is not None
    assert resolved.record.human_answer is LeaveHumanVerdict.KEEP
    assert resolved.record.persisted is True


async def test_resolve_discard_does_not_persist(tmp_path: Path) -> None:
    decision = _ask_decision(LeaveVerdict.ASK)
    asker = FakeAsker(answer=_answer(chosen_option=2))
    leave = with_asker(
        build_leave_context(_cell(), load_leave_policy(), (), None, tmp_path),
        asker,
        _TIMEOUT_S,
        FakeClock(),
    )
    check = HumanCheck(FakeClock(), _TIMEOUT_S)
    proposal = make_proposal(risk_tier=RiskTier.OUTSIDE_SCRATCH_WRITE)

    resolved = await check.resolve(leave, decision, proposal)

    assert resolved.persist is False
    assert resolved.approved_by is None
    assert resolved.record is not None
    assert resolved.record.human_answer is LeaveHumanVerdict.DISCARD
    assert resolved.record.persisted is False
