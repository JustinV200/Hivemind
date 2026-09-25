"""End-to-end: a Guard Bee raise of a Capping tier's audit rate reaches a Virtual Cell's own gate.

Roadmap step 10.6 on a real run. The Hive is composed by `build_hive` with a Virtual side over the
container-spawning fake backend, so every Virtual Cell runs a real in-Cell Warden whose trail is
its own segment, which never sees the Queen's. Sampled audits of SCRATCH_WRITE start failing on the
Queen's trail (recorded as a Warden records them); the composed Hive's own Guard Bee raises the
tier's audit rate to 1.0 there. A goal then lands on a Virtual Cell: the grant the Queen issues for
it carries the raise (Waggle 1.10's `GrantIssued.audit_raises`), and the in-Cell Warden's gate
samples every one of its Drone's writes, where the shipped tier table alone samples 2 in 100. The
Cell's `capping.audited` rows reach the Queen's trail in its shipped segment. The in-Cell Warden's
gate has the model-backed judge its slot table binds (phase 6), so each sample is judged inside
the Cell, never recorded inconclusive (`judge_error`), and never crashes the Drone (the run found
that it did).

Fits into the Hive:
    Test infrastructure (codingrules section 14.2), not shipped.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.queen.forage.grants.grant_message and hivemind.supervision.capping.audit.raises.
    - tests.e2e.test_virtual_cells for the Virtual side this builds on.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from builders.guard_bee import TrailSeeder, quick_rounds
from builders.virtual_cells import (
    ContainerSpawningFakeCellBackend,
    VirtualCellsTuning,
    single_haiku_plan,
    virtual_cells_manifest,
)
from e2e.kernel_helpers import HaikuScript, default_worker_turn, wait_until

from hivemind.cell import CellIdentity, HoneyClearance
from hivemind.cli.compose import Hive, build_hive, run_goal, run_hive
from hivemind.manifest import load_manifest
from hivemind.pheromone import CappingEvent, PheromoneEvent, TrailQuery
from waggle.clock import SystemClock
from waggle.ids import new_message_id

pytestmark = pytest.mark.e2e

_TIMEOUT_S = 20.0  # Each wait; every step lands within a second or two.
_TIER = "SCRATCH_WRITE"  # Shipped at a 0.02 sampled-audit rate.
_FAILING_AUDITS = ("REJECT", "APPROVE", "APPROVE", "APPROVE")  # A 25 % failure rate: raised.


async def _events(hive: Hive, kind: str) -> list[PheromoneEvent]:
    """Every `kind` event on the Queen's trail, oldest first."""
    return list(await hive.stores.trail.query(TrailQuery(kind=kind)))


async def _raised(hive: Hive) -> bool:
    """Whether the Guard Bee has raised a tier's audit rate yet."""
    return bool(await _events(hive, "guard.audit_rate_raised"))


async def _audits_fail(hive: Hive) -> None:
    """Record sampled audits of the tier failing, as a Warden's gate records them."""
    manifest = hive.manifest
    identity = CellIdentity(manifest.hive.id, manifest.hive.node_id, "system")
    seed = TrailSeeder(hive.stores.trail, hive.clock, identity)
    for outcome in _FAILING_AUDITS:
        proposal = new_message_id(hive.clock)
        payload = {"tier": _TIER, "outcome": outcome, "rubric_id": "scratch_write_v1"}
        await seed.record(CappingEvent, "capping.audited", proposal, payload)


async def _scenario(hive: Hive) -> tuple[list[PheromoneEvent], list[PheromoneEvent]]:
    """Raise the tier, run one goal on a Virtual Cell, and return its proposals and audits."""
    async with run_hive(hive):
        await _audits_fail(hive)
        await wait_until(lambda: _raised(hive), timeout_s=_TIMEOUT_S)
        report = await run_goal(
            hive, "write one haiku", clearance=HoneyClearance.C1, timeout_s=_TIMEOUT_S
        )
        assert report.succeeded, report
    node = hive.manifest.hive.node_id
    proposed = [e for e in await _events(hive, "capping.proposed") if e.node_id != node]
    audited = [e for e in await _events(hive, "capping.audited") if e.node_id != node]
    return proposed, audited


def test_a_raise_reaches_a_virtual_cells_own_gate_on_its_grant(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "hivemind.cli.compose.virtual_cell_backends.FakeCellBackend",
        ContainerSpawningFakeCellBackend,
    )
    tuning = VirtualCellsTuning(prefer="virtual", allow_hive_stand=False)
    manifest_path = virtual_cells_manifest(tmp_path, tuning=tuning)
    manifest = load_manifest(quick_rounds(manifest_path, extra="audit_raise_step = 1.0\n"), {})
    script = HaikuScript(default_worker_turn, plan=single_haiku_plan("haiku_1.txt"))
    hive = build_hive(
        manifest, environ={}, clock=SystemClock(), responders={"fake": script.responder}
    )

    proposed, audited = asyncio.run(_scenario(hive))

    [raised] = asyncio.run(hive.stores.trail.query(TrailQuery(kind="guard.audit_rate_raised")))
    assert (raised.payload["tier"], raised.payload["to_rate"]) == (_TIER, 1.0)
    # Every write the in-Cell Drone proposed was sampled inside the Cell, and shipped back.
    assert proposed and {e.payload["tier"] for e in proposed} == {_TIER}
    assert {e.subject_id for e in audited} == {e.subject_id for e in proposed}
    # Judged in the Cell by its own judge: a verdict on every sample, none left inconclusive.
    assert all(e.payload["outcome"] is not None for e in audited)
    assert not any("judge_error" in e.payload for e in audited)
