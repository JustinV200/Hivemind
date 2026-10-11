"""End-to-end: a provider outage mid-goal pauses the Drone with a checkpoint; the wake resumes it.

Phase 8's Clustering criterion under `offline.toml`, with the real thing in the provider's seat: the
model is the scripted loopback server, and its outage is real (the server answers 503 to every
request while it is switched off, and health probes read DEGRADED, not healthy). The goal is two
files, written one tool round each. The server parks the Drone's second model call, standing for a
reply in flight when the provider goes away. The test then orders `hive cluster local` through the
Queen's order table (the durable row `hive cluster` writes) and releases the parked reply once the
Drone's runtime has been told to hand off; the Drone, back at its next tool call, checkpoints
instead of acting, and only then is the task paused. Asserted: `memory.checkpoint` is on the trail
before `task.paused`; the task stays paused while the server is down; and after the server is back
and `hive wake` is ordered the resumed Drone is given the Handoff, so each file is written exactly
once across both attempts (the script skips a write the Handoff says already landed, and the
Capping gate applied one write per file). The goal succeeds, every `llm.call` is local and no
socket left loopback.

Fits into the Hive:
    Test infrastructure (codingrules section 14.2), not shipped.

Key invariants:
    - The only outage is the server's: nothing here marks a provider down by writing to the Queen's
      health state.

See Also:
    - tests/e2e/test_clustering.py for the scripted-Worker version (roadmap 4.9).
    - docs/adr/0024-clustering-protocol.md for the protocol.
"""

from __future__ import annotations

import asyncio
from collections import Counter
from pathlib import Path

import pytest
from e2e.kernel_helpers import wait_until
from e2e.offline import EgressLog, assert_every_call_local, offline_manifest
from e2e.offline.rig import GOAL, TIMEOUT_S
from e2e.scripted_openai import Scenario, Step, Switches, serve_in_thread

from hivemind.cell import HoneyClearance
from hivemind.cli.compose import Hive, build_hive, run_goal, run_hive
from hivemind.pheromone import MAX_QUERY_LIMIT, PheromoneEvent, TrailQuery
from hivemind.queen.cluster.orders import ClusterOrder, OrderKind, new_order_id
from waggle.clock import SystemClock

pytestmark = pytest.mark.e2e

_FILES = ("first.txt", "second.txt")  # Written one per tool round, in this order.
_PROVIDER = "local"  # The offline manifest's one chat provider.
_ROUND_AFTER_FIRST_WRITE = 1  # The Drone's second model call: one tool result already in hand.


def test_a_provider_outage_pauses_with_a_checkpoint_and_the_wake_resumes_without_rewriting(
    tmp_path: Path, egress: EgressLog
) -> None:
    steps = tuple(Step("write_file", {"path": name, "content": "bees"}) for name in _FILES)
    scenario = Scenario(files=_FILES, steps=steps)
    with serve_in_thread(scenario) as base_url:
        manifest, _path = offline_manifest(tmp_path, base_url)
        hive = build_hive(manifest, environ={}, clock=SystemClock())

        succeeded, events = asyncio.run(_cluster_and_wake(hive, scenario.switches))

    assert succeeded
    kinds = [event.kind for event in events]
    assert kinds.index("memory.checkpoint") < kinds.index("task.paused")
    assert kinds.index("task.paused") < kinds.index("task.resumed")
    # No duplicated work: each file was written once across both attempts (the first attempt's
    # parked reply asked for the second file, but the Drone checkpointed instead of writing it) ...
    assert Counter(e.kind for e in events)["capping.applied"] == len(_FILES)
    # ... and the resumed attempt, handed the Handoff, began at the second file, not the first.
    openings = [s.issued for s in scenario.switches.shapes if s.rounds == 0 and s.issued]
    assert openings == [(f"write_file:{_FILES[0]}",), (f"write_file:{_FILES[1]}",)], openings
    assert_every_call_local(events, manifest)
    egress.assert_clean_and_live()


async def _cluster_and_wake(
    hive: Hive, switches: Switches
) -> tuple[bool, tuple[PheromoneEvent, ...]]:
    """Run the goal, take the provider away mid-goal, cluster, bring it back, wake."""
    switches.hold_at = _ROUND_AFTER_FIRST_WRITE
    async with run_hive(hive):
        goal = asyncio.ensure_future(
            run_goal(hive, GOAL, clearance=HoneyClearance.C1, timeout_s=TIMEOUT_S)
        )
        await wait_until(switches.arrived.is_set, timeout_s=TIMEOUT_S)  # Reply in flight.
        switches.outage.set()  # The server is gone from here on.
        await _order(hive, OrderKind.CLUSTER)
        await wait_until(lambda: _told_to_hand_off(hive), timeout_s=TIMEOUT_S)
        switches.release.set()  # The reply lands; the Drone meets the handoff at its next call.
        await wait_until(lambda: _recorded(hive, "task.paused"), timeout_s=TIMEOUT_S)
        await _stays_paused(hive)
        switches.outage.clear()  # The provider is back; the operator wakes the Hive.
        await _order(hive, OrderKind.WAKE)
        report = await goal
    events = await hive.stores.trail.query(TrailQuery(limit=MAX_QUERY_LIMIT))
    return report.succeeded, tuple(events)


async def _order(hive: Hive, kind: OrderKind) -> None:
    """Write the durable order `hive cluster` / `hive wake` write; the running Queen polls it."""
    deps = hive.queen_deps
    await deps.orders.put_order(
        ClusterOrder(
            id=new_order_id(deps.clock),
            kind=kind,
            provider=_PROVIDER,
            requested_at=deps.clock.now(),
        )
    )


def _told_to_hand_off(hive: Hive) -> bool:
    """Whether the Queen's Intervene(HANDOFF) has reached a running Drone's telemetry.

    The one place this suite reads a runtime's private context: nothing else says the order has
    been carried out while the Queen is still waiting on the checkpoint, and releasing the parked
    reply any earlier would let the Drone finish its work before it is told to stop.
    """
    return any(bee.runtime._ctx.telemetry.handoff_requested for bee in hive.warden.sub_bees)


async def _recorded(hive: Hive, kind: str) -> bool:
    """Whether the trail holds a `kind` event yet."""
    return bool(await hive.stores.trail.query(TrailQuery(kind=kind)))


async def _stays_paused(hive: Hive) -> None:
    """Give the Queen's health poller a few ticks while the server is down; it must not resume."""
    await asyncio.sleep(0.5)
    assert not await _recorded(hive, "task.resumed")
