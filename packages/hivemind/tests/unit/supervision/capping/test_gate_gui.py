"""Unit tests for the Capping gate's GUI path: the injected GuiSurface's five moves (ADR-0032).

Fits into the Hive:
    Mirrors src/hivemind/supervision/capping/gate/gui.py and the GUI branches of gate/core.py,
    apply.py and checks/deterministic.py (codingrules section 3), split by feature (14.2) from
    test_gate.py.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.supervision.capping.gui for the GuiSurface protocol builders.gui's surface follows.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from builders.capping import FakeLeaseView, make_action, make_postcondition, make_proposal
from builders.cells import make_cell, make_identity
from builders.gui import ScriptedSurface

from hivemind.cell import CellKind, FakeSession, NoopSnapshotter
from hivemind.guard import CapabilitySet
from hivemind.pheromone import MemoryPheromoneTrail, TrailQuery
from hivemind.supervision.capping import (
    GateOutcome,
    Proposal,
    ProposalState,
    RiskTier,
    TierSpec,
    TierTable,
    deterministic_checks,
    required_capabilities,
)
from hivemind.supervision.capping.gate import CappingGate, GateDeps
from hivemind.supervision.capping.gate.gui import NO_SURFACE
from waggle.clock import FakeClock
from waggle.messages.capping import (
    ActionKind,
    CheckKind,
    ElementTarget,
    GuiOp,
    GuiStep,
)
from waggle.messages.labels import PostconditionKind

_LOG_IN = GuiStep(op=GuiOp.BROWSER_CLICK, target=ElementTarget(role="button", name="Log in"))
_ARRIVED = make_postcondition(
    PostconditionKind.URL_MATCHES, subject="page", expected="file:///site/welcome.html*"
)
_BROWSER = CapabilitySet.parse("exoskeleton:browser")
# Both tiers GUI tools declare in these tests; NETWORK_EGRESS runs the allowlist rung.
_TIERS = TierTable(
    tiers={
        RiskTier.SCRATCH_WRITE: TierSpec(
            checks=(CheckKind.SCHEMA, CheckKind.SIZE_CAP), floor=(CheckKind.SCHEMA,)
        ),
        RiskTier.NETWORK_EGRESS: TierSpec(
            checks=(CheckKind.SCHEMA, CheckKind.ALLOWLIST), floor=(CheckKind.SCHEMA,)
        ),
    }
)


def _gate(
    tmp_path: Path, surface: ScriptedSurface | None
) -> tuple[CappingGate, MemoryPheromoneTrail]:
    clock = FakeClock()
    trail = MemoryPheromoneTrail(clock)
    deps = GateDeps(
        session=FakeSession(tmp_path, clock),
        snapshotter=NoopSnapshotter(),
        cell=make_cell(kind=CellKind.REAL, clock=clock),
        tiers=_TIERS,
        trail=trail,
        identity=make_identity(clock),
        clock=clock,
        checks=deterministic_checks(),
        gui=surface,
    )
    return CappingGate(deps), trail


def _gui_proposal(*steps: GuiStep, tier: RiskTier = RiskTier.SCRATCH_WRITE) -> Proposal:
    action = make_action(ActionKind.GUI, gui=steps or (_LOG_IN,), steps=())
    return make_proposal(tier, action=action, postconditions=(_ARRIVED,))


async def _run(
    gate: CappingGate, proposal: Proposal, caps: CapabilitySet = _BROWSER, tmp: Path = Path("/s")
) -> GateOutcome:
    await gate.propose(proposal)
    return await gate.run(proposal.id, caps, FakeLeaseView(tmp))


async def _kinds(trail: MemoryPheromoneTrail) -> list[str]:
    return [event.kind for event in await trail.query(TrailQuery())]


async def test_a_gui_proposal_with_no_surface_is_rejected_before_any_check(tmp_path: Path) -> None:
    gate, trail = _gate(tmp_path, None)

    outcome = await _run(gate, _gui_proposal(), tmp=tmp_path)

    assert outcome.state is ProposalState.REJECTED
    assert outcome.reason == NO_SURFACE
    assert "capping.checked" not in await _kinds(trail)


async def test_a_gui_proposal_is_prepared_applied_verified_and_finished_by_the_surface(
    tmp_path: Path,
) -> None:
    surface = ScriptedSurface()
    gate, _ = _gate(tmp_path, surface)

    outcome = await _run(gate, _gui_proposal(), tmp=tmp_path)

    assert outcome.state is ProposalState.VERIFIED
    assert surface.calls == ["before", "apply", "check URL_MATCHES", "finish VERIFIED None"]


async def test_a_failed_gui_postcondition_is_rolled_back_by_gui_state(tmp_path: Path) -> None:
    surface = ScriptedSurface(holds=False)
    gate, trail = _gate(tmp_path, surface)

    outcome = await _run(gate, _gui_proposal(), tmp=tmp_path)

    assert outcome.state is ProposalState.ROLLED_BACK
    assert surface.calls[-2:] == ["restore", "finish ROLLED_BACK GUI_STATE"]
    rolled = [e for e in await trail.query(TrailQuery()) if e.kind == "capping.rolled_back"]
    assert rolled[0].payload == {"method": "GUI_STATE"}


async def test_a_failed_step_rolls_back_without_checking_postconditions(tmp_path: Path) -> None:
    surface = ScriptedSurface(applies=False)
    gate, _ = _gate(tmp_path, surface)

    outcome = await _run(gate, _gui_proposal(), tmp=tmp_path)

    assert outcome.state is ProposalState.ROLLED_BACK
    assert outcome.reason == "step 1 failed"
    assert not any(call.startswith("check") for call in surface.calls)


async def test_nothing_to_restore_rolls_back_with_no_method(tmp_path: Path) -> None:
    surface = ScriptedSurface(holds=False, restores=False)
    gate, _ = _gate(tmp_path, surface)

    await _run(gate, _gui_proposal(), tmp=tmp_path)

    assert surface.calls[-1] == "finish ROLLED_BACK NONE"


async def test_a_proposal_that_uses_no_gui_never_touches_the_surface(tmp_path: Path) -> None:
    surface = ScriptedSurface()
    gate, _ = _gate(tmp_path, surface)
    proposal = make_proposal(
        action=make_action(ActionKind.COMMAND),
        postconditions=(make_postcondition(PostconditionKind.COMMAND_EXITS_ZERO),),
    )

    await _run(gate, proposal, tmp=tmp_path)

    assert surface.calls == []


async def test_the_allowlist_rung_needs_the_browser_scope_and_net_for_egress(
    tmp_path: Path,
) -> None:
    surface = ScriptedSurface()
    gate, _ = _gate(tmp_path, surface)
    away = GuiStep(op=GuiOp.NAVIGATE, url="https://example.org/login")

    no_browser = await _run(
        gate,
        _gui_proposal(tier=RiskTier.NETWORK_EGRESS),
        CapabilitySet.parse("exoskeleton:display"),
        tmp_path,
    )
    no_net = await _run(gate, _gui_proposal(away, tier=RiskTier.NETWORK_EGRESS), _BROWSER, tmp_path)
    allowed = await _run(
        gate,
        _gui_proposal(away, tier=RiskTier.NETWORK_EGRESS),
        CapabilitySet.parse("exoskeleton:browser", "net:example.org"),
        tmp_path,
    )

    assert (no_browser.state, no_net.state) == (ProposalState.REJECTED, ProposalState.REJECTED)
    assert "exoskeleton:browser" in no_browser.reason
    assert "net:example.org" in no_net.reason
    assert allowed.state is ProposalState.VERIFIED


@pytest.mark.parametrize(
    ("step", "scopes"),
    [
        (GuiStep(op=GuiOp.CLICK, x=1, y=2), {"exoskeleton:display"}),
        (GuiStep(op=GuiOp.SAY, clip="hi.wav"), {"exoskeleton:audio"}),
        (GuiStep(op=GuiOp.NAVIGATE, url="file:///s/site/index.html"), {"exoskeleton:browser"}),
        (
            GuiStep(op=GuiOp.NAVIGATE, url="http://localhost:8000/"),
            {"exoskeleton:browser", "net:localhost"},
        ),
    ],
)
def test_required_capabilities_per_step(step: GuiStep, scopes: set[str]) -> None:
    assert {str(cap) for cap in required_capabilities(step)} == scopes
