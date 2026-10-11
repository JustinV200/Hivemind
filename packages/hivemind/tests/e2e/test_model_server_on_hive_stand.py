"""End-to-end: the Hive Stand's Warden starts a real model server, the Hive uses it, then stops it.

Roadmap step 8.3's real task (ADR-0044). The manifest declares `[llm.providers.local]` (an
`openai_compat` provider on a loopback port) and `[llm.servers.local]`, whose argv runs the
scripted OpenAI-compatible server (`tests/e2e/scripted_openai.py`, as its own process) on that
port; every model slot is bound to `local`. `build_hive` composes the Hive Stand's Warden with the
server table, and `run_hive` starts it: the Warden leases its Cell, starts the server through the
Cell's real `CellSession`, waits for the provider's own health probe, adds the server's Forage
source to the live map with the Hive Stand's Cell as host, and tells the Queen, who records it. The
Queen then plans the haiku goal and a Drone writes its files through that very server, so every
`llm.call` on the trail names the provider `local`. When `run_hive` exits nothing the Warden
started is alive, the source is off the map, and `forage.source_removed` is on the trail.

Fits into the Hive:
    Test infrastructure (codingrules section 14.2), not shipped.

Key invariants:
    - The server's pid is a real operating-system process the test checks is dead afterwards
      (`kernel_helpers.pid_alive`), never a fake.
    - No server program name appears here: the argv is composed from `sys.executable` and the
      scripted server module's own path.

See Also:
    - tests/e2e/scripted_openai.py for the server this test starts as a process.
    - tests/e2e/test_kernel_on_hive_stand.py for the whole-Hive style this follows.
    - hivemind.wardens.local_pool.hosting for what is exercised.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest
from e2e import scripted_openai
from e2e.kernel_helpers import pid_alive, wait_until

from hivemind.cell import HoneyClearance
from hivemind.cli.compose import Hive, build_hive, run_goal, run_hive
from hivemind.forage import UnknownSourceError
from hivemind.llm import HealthState
from hivemind.manifest import load_manifest
from hivemind.pheromone import PheromoneEvent, TrailQuery
from waggle.clock import SystemClock
from waggle.ids import new_hive_id, new_node_id

pytestmark = pytest.mark.e2e

_GOAL = "write three haiku about bees to separate files"
_FILES = ("haiku_1.txt", "haiku_2.txt", "haiku_3.txt")
_SOURCE_ID = "scripted_local"  # The [forage.map] row scripted_openai.write_manifest declares.
_TIMEOUT_S = 30.0  # Generous: a real server starts in well under a second.


def _manifest_path(tmp_path: Path) -> Path:
    """Write a manifest whose one provider, `local`, is served by a Warden-started process."""
    port = scripted_openai.free_port()
    clock = SystemClock()
    written = scripted_openai.write_manifest(
        tmp_path, f"http://127.0.0.1:{port}/v1", (new_hive_id(clock), new_node_id(clock))
    )
    # The scripted manifest names its one provider `scripted`; this scenario calls it `local`.
    text = written.read_text(encoding="utf-8")
    text = text.replace("[llm.providers.scripted", "[llm.providers.local").replace(
        'provider = "scripted"', 'provider = "local"'
    )
    scenario = tmp_path / "scenario.json"
    scenario.write_text(json.dumps({"files": list(_FILES)}), encoding="utf-8")
    argv = [
        sys.executable,
        str(Path(scripted_openai.__file__).resolve()),
        "--port",
        "{port}",
        "--scenario",
        str(scenario),
    ]
    written.write_text(
        text
        + "\n[llm.servers.local]\n"
        + 'provider = "local"\n'
        + f"argv = {json.dumps(argv)}\n"
        + f"port = {port}\n"
        + "startup_timeout_s = 30.0\n"
        + f'sources = ["{_SOURCE_ID}"]\n',
        encoding="utf-8",
    )
    return written


async def _forage_events(hive: Hive, kind: str, reporter: str) -> list[PheromoneEvent]:
    """Every `kind` row `reporter` ("warden" or "queen") recorded on the trail so far."""
    events = await hive.stores.trail.query(TrailQuery())
    return [e for e in events if e.kind == kind and e.payload.get("reporter") == reporter]


def test_a_warden_started_model_server_serves_a_goal_and_leaves_nothing_behind(
    tmp_path: Path,
) -> None:
    manifest = load_manifest(_manifest_path(tmp_path), {})
    hive = build_hive(manifest, environ={}, clock=SystemClock())

    asyncio.run(_run(hive))


async def _run(hive: Hive) -> None:
    """Run the haiku goal on the served Hive, checking the server's whole life around it."""
    async with run_hive(hive):
        # The Warden started the server after it held its lease, before the goal began.
        servers = hive.warden.hosted_servers
        assert set(servers) == {"local"}
        pid = servers["local"].process.pid
        assert pid_alive(pid)

        # Its provider is healthy, and the source is on the live Forage map, hosted on this Cell.
        health = await hive.registry.provider("local").health()
        assert health.state is HealthState.HEALTHY
        source = hive.fanner.deps.map.get(_SOURCE_ID)
        assert source.spec.host_cell_id == hive.warden_link.cell.id

        # The Queen learned of it from the Warden's report and recorded it on her side.
        await wait_until(lambda: _has(hive, "forage.source_added", "queen"), timeout_s=_TIMEOUT_S)
        assert [
            e.payload["source_id"]
            for e in await _forage_events(hive, "forage.source_added", "warden")
        ] == [_SOURCE_ID]

        report = await run_goal(hive, _GOAL, clearance=HoneyClearance.C1, timeout_s=_TIMEOUT_S)
        assert report.succeeded, report

    await _check_after_exit(hive, pid)


async def _has(hive: Hive, kind: str, reporter: str) -> bool:
    """Whether `reporter` has recorded a `kind` row yet."""
    return bool(await _forage_events(hive, kind, reporter))


async def _check_after_exit(hive: Hive, pid: int) -> None:
    """After `run_hive` returned: every call went to `local`, and nothing was left running."""
    events = await hive.stores.trail.query(TrailQuery())
    calls = [e for e in events if e.kind == "llm.call"]
    # The Queen, the Drone and the judge all went through the one served model.
    assert calls and {getattr(e, "provider", None) for e in calls} == {"local"}
    assert any(getattr(e, "slot", None) == "WORKER" for e in calls)

    # Left as found: the process is gone, the source is off the map, and the removal is recorded
    # from the Warden's side with the cause the lease's end gives.
    assert not pid_alive(pid)
    assert hive.warden.hosted_servers == {}
    with pytest.raises(UnknownSourceError):
        hive.fanner.deps.map.get(_SOURCE_ID)
    removed = await _forage_events(hive, "forage.source_removed", "warden")
    assert [(e.payload["source_id"], e.payload["cause"]) for e in removed] == [
        (_SOURCE_ID, "LEASE_RELEASED")
    ]
