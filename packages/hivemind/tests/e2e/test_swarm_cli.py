"""End-to-end test of the operator's `hive swarm` commands against a real Hive and a real device.

Roadmap step 11.12 on the same real task as step 11.6: a device is brought into the Swarm, used and
taken back, but by the commands an operator types rather than by the library calls the level 0
scenario makes. A real `pollen` packet runs in this process and dials a real Hive (the real Queen,
swarm listener and SQLite file, a scripted OpenAI-compatible model server behind it). Two scenarios
cover the whole command group:

1. Onboarding and revocation. `hive swarm invite` mints the string, the packet enrols with it,
   `hive swarm list` and `hive cells list` show the device as it is (online, on the first rung,
   its Warden on the Hive Stand), `hive swarm access` narrows it and the running Hive passes the
   change to the packet, and `hive swarm revoke` cuts the device off: the Hive stops its Warden and
   closes its link, and the listings show it revoked and no longer a Cell.
2. A goal, on that device and nowhere else. The Hive that enrolled the device stops (the operator
   stops `hive serve`), `hive swarm run NODE "GOAL"` builds a Hive of its own, the packet dials
   it, and the goal is placed on the device's Cell, run there by the Hive's Drone through the
   session the Warden leased and verified there before the task could succeed. The lease's scratch
   is gone from the device afterwards and every lease opened for the goal is on the device's Cell.

Fits into the Hive:
    Test infrastructure (codingrules section 14.2), not shipped.

Key invariants:
    - None: this module holds tests only.

See Also:
    - tests/e2e/test_swarm_level0.py for the same device driven through the library.
    - e2e/swarm/ for the packet and Hive scaffolding this uses.
    - hivemind.cli.swarm for the commands under test.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
from e2e.kernel_helpers import wait_until
from e2e.scripted_openai import Scenario, free_port, serve_in_thread, write_manifest
from e2e.swarm.device import EnrolledDevice, enrol, running
from e2e.swarm.hive import attached_warden_ids, enrolled_node, trail_events
from structlog.testing import capture_logs
from typer.testing import CliRunner, Result

from hivemind.cell import AccessLevel
from hivemind.cli.app import app
from hivemind.cli.compose import Hive, build_hive, run_hive
from hivemind.cli.stores import open_trail
from hivemind.manifest import load_manifest
from hivemind.pheromone import PheromoneEvent, TrailQuery
from hivemind.swarm import NodeStatus, SwarmNode
from hivemind.wardens import WardenState
from pollen.state import load_state
from waggle.clock import SystemClock
from waggle.ids import LeaseId, new_hive_id, new_node_id

pytestmark = pytest.mark.e2e

_WAIT_S = 30.0  # Each wait; a device enrols, connects and runs its task in a couple of seconds.
_GOAL_TIMEOUT_S = 90  # What `hive swarm run` is given; a goal here takes a few seconds.
_LABEL = "e2e-laptop"
_GOAL = "write a report on the borrowed device"
_EVENT_LIMIT = 500  # More trail events than a scenario records.


def _manifest(directory: Path, base_url: str) -> Path:
    """Write a Hive Manifest on the scripted model server, its Swarm on a free loopback port."""
    clock = SystemClock()
    path = write_manifest(directory, base_url, (new_hive_id(clock), new_node_id(clock)))
    swarm = f"\n[swarm]\nenabled = true\nlisten_port = {free_port()}\nheartbeat_interval_s = 0.25\n"
    path.write_text(path.read_text(encoding="utf-8") + swarm, encoding="utf-8")
    return path


def _hive(manifest: Path) -> Hive:
    """Build the Hive a manifest describes, as `hive serve` does; not yet started."""
    return build_hive(load_manifest(manifest, {}), environ={}, clock=SystemClock())


async def _operator(*command: str) -> Result:
    """Run one `hive` command as the operator types it, off the loop the packet runs on.

    The commands that build a Hive run their own event loop, which a worker thread has and this
    thread does not. Structlog's lines are captured so that standard output holds only what the
    command prints.
    """

    def invoke() -> Result:
        with capture_logs():
            return CliRunner().invoke(app, list(command))

    return await asyncio.to_thread(invoke)


async def _swarm(manifest: Path, *arguments: str) -> Result:
    """Run `hive swarm --manifest M ARGUMENTS`."""
    return await _operator("swarm", "--manifest", str(manifest), *arguments)


def _rows_of(result: Result) -> list[dict[str, Any]]:
    """The rows a listing printed with `--json`; the command must have succeeded."""
    assert result.exit_code == 0, result.stderr
    rows: list[dict[str, Any]] = json.loads(result.stdout)
    return rows


async def _devices(manifest: Path) -> list[dict[str, Any]]:
    """The rows of `hive swarm list --json`."""
    return _rows_of(await _swarm(manifest, "list", "--json"))


async def _cells(manifest: Path) -> list[dict[str, Any]]:
    """The rows of `hive cells list --json`."""
    return _rows_of(await _operator("cells", "list", "--manifest", str(manifest), "--json"))


def _read_events(manifest: Path, kind: str) -> list[PheromoneEvent]:
    """Every event of `kind` on the Hive's trail, read from its database file as a process would."""
    trail = open_trail(manifest.parent / "data" / "hive.sqlite3")
    return list(asyncio.run(trail.query(TrailQuery(kind=kind, limit=_EVENT_LIMIT))))


async def _events(manifest: Path, kind: str) -> list[PheromoneEvent]:
    """`_read_events` on a worker thread, which has the event loop that opening the trail runs."""
    return await asyncio.to_thread(_read_events, manifest, kind)


async def _invite_and_enrol(
    manifest: Path, hive: Hive, data_dir: Path, access: str
) -> EnrolledDevice:
    """Mint an invite with `hive swarm invite`, enrol a packet with it and return the device."""
    invited = await _swarm(manifest, "invite", "--access", access, "--label", _LABEL)
    assert invited.exit_code == 0, invited.stderr
    # The string is the only thing on standard output; everything else the command says is on
    # standard error, so that `hive swarm invite > file` keeps the secret alone.
    invite = invited.stdout.strip()
    assert invite.startswith("pollen1.") and "\n" not in invite
    return await enrol(invite, data_dir, hive.clock)


def test_an_operator_enrols_lists_narrows_and_revokes_a_device_with_the_swarm_commands(
    tmp_path: Path,
) -> None:
    with serve_in_thread(Scenario()) as base_url:
        manifest = _manifest(tmp_path, base_url)
        hive = _hive(manifest)

        asyncio.run(_onboard_and_revoke(manifest, hive, tmp_path / "device"))


async def _onboard_and_revoke(manifest: Path, hive: Hive, data_dir: Path) -> None:
    """Enrol a device, read it back, narrow its access, then revoke it, all by command."""
    async with run_hive(hive):
        device = await _invite_and_enrol(manifest, hive, data_dir, "scratch")
        node = await enrolled_node(hive)
        async with running(device):
            await wait_until(lambda: _online(hive, node), timeout_s=_WAIT_S)
            await _the_listings_show_the_device_as_it_is(manifest, node)
            await _access_reaches_the_packet(manifest, hive, device, node)
            await _revoking_cuts_the_device_off(manifest, hive, node)


async def _online(hive: Hive, node: SwarmNode) -> bool:
    """Whether the Hive's own link to the device is up and its Warden is attached to the Queen."""
    assert hive.swarm is not None
    linked = hive.swarm.listener.hub.link(node.device_id) is not None
    return linked and node.warden_id in attached_warden_ids(hive)


async def _the_listings_show_the_device_as_it_is(manifest: Path, node: SwarmNode) -> None:
    """`hive swarm list` and `hive cells list` agree with what enrolment made."""
    (listed,) = await _devices(manifest)
    assert (listed["device_id"], listed["label"], listed["cell_id"]) == (
        node.device_id,
        _LABEL,
        node.cell_id,
    )
    assert (listed["status"], listed["level"], listed["access_level"]) == (
        "ONLINE",
        "GATEWAY",
        "SCRATCH",
    )

    cells = {row["source"]: row for row in await _cells(manifest)}
    # The device is a Real Cell of source swarm, its Warden still on the Hive Stand at this rung.
    assert set(cells) == {"hive_stand", "swarm"}
    device = cells["swarm"]
    assert (device["id"], device["name"], device["kind"]) == (node.cell_id, _LABEL, "REAL")
    assert (device["level"], device["warden_runs"], device["enabled"]) == (
        "GATEWAY",
        "hive_stand",
        True,
    )


async def _access_reaches_the_packet(
    manifest: Path, hive: Hive, device: EnrolledDevice, node: SwarmNode
) -> None:
    """`hive swarm access` writes the registry and trail, and the running Hive tells the packet."""
    changed = await _swarm(manifest, "access", _LABEL, "read_only")
    assert changed.exit_code == 0, changed.stderr

    assert hive.swarm is not None
    stored = await hive.swarm.registry.node(node.device_id)
    assert stored is not None and stored.access_level is AccessLevel.READ_ONLY
    (event,) = await _events(manifest, "swarm.access_changed")
    assert (event.payload["from_level"], event.payload["to_level"]) == ("SCRATCH", "READ_ONLY")
    # The Hive's next beat sends the packet its new terms; the packet keeps them, and acts on them
    # from its next request.
    await wait_until(
        # The packet's own enum is the wire one; its value is what the Hive's spells the same way.
        lambda: load_state(device.layout.state).access_level.value == AccessLevel.READ_ONLY.value,
        timeout_s=_WAIT_S,
    )
    # The same pass rebuilds the device's Warden so that its Cell carries the new terms, and the new
    # Warden leases the device once and gives the lease back when it finds nothing to do. Revoking
    # before then would find the Warden between its stop and its start, or holding a lease that the
    # revoke's closed link would leave to the device's dead-man switch.
    await wait_until(
        lambda: _idle_warden_built_for(hive, node, AccessLevel.READ_ONLY), timeout_s=_WAIT_S
    )
    (listed,) = await _devices(manifest)
    assert listed["access_level"] == "READ_ONLY"


def _idle_warden_built_for(hive: Hive, node: SwarmNode, level: AccessLevel) -> bool:
    """Whether the device's Warden was built with `level`, is attached again and holds no lease."""
    assert hive.swarm is not None
    served = hive.swarm.gate.wardens.get(node.device_id)
    if served is None or served.node.access_level is not level:
        return False
    warden = served.warden
    idle = warden.lease is None and warden.state is WardenState.WATCH
    return idle and node.warden_id in attached_warden_ids(hive)


async def _revoking_cuts_the_device_off(manifest: Path, hive: Hive, node: SwarmNode) -> None:
    """`hive swarm revoke` closes the link, stops the device's Warden and removes its Cell."""
    assert hive.swarm is not None
    gate = hive.swarm.gate
    served = gate.wardens.get(node.device_id)
    assert served is not None
    warden = served.warden

    revoked = await _swarm(manifest, "revoke", _LABEL)
    assert revoked.exit_code == 0, revoked.stderr

    # The running Hive, not the command, ends the link: the gate's next pass sees the status.
    await wait_until(lambda: node.warden_id not in attached_warden_ids(hive), timeout_s=_WAIT_S)
    assert gate.wardens.get(node.device_id) is None
    assert warden.state is WardenState.STOPPED
    assert hive.swarm.listener.hub.link(node.device_id) is None
    assert hive.warden_link.warden_id in attached_warden_ids(hive)  # The Hive Stand's carries on.
    (event,) = await trail_events(hive, "swarm.revoked")
    assert event.payload["device_id"] == node.device_id
    (listed,) = await _devices(manifest)
    assert listed["status"] == NodeStatus.REVOKED.value
    # A revoked device is no longer a Cell, though the audit still lists it.
    assert [row["source"] for row in await _cells(manifest)] == ["hive_stand"]


def test_hive_swarm_run_places_the_goal_on_the_named_device_and_runs_it_there(
    tmp_path: Path,
) -> None:
    with serve_in_thread(Scenario(files=("report.txt",))) as base_url:
        manifest = _manifest(tmp_path, base_url)
        hive = _hive(manifest)

        asyncio.run(_enrol_then_run(manifest, hive, tmp_path / "device"))


async def _enrol_then_run(manifest: Path, hive: Hive, data_dir: Path) -> None:
    """Enrol a device into one Hive, stop that Hive, and run a goal on the device with another."""
    async with run_hive(hive):
        device = await _invite_and_enrol(manifest, hive, data_dir, "full")
        node = await enrolled_node(hive)
    # The Hive that enrolled the device has stopped, as the operator stops `hive serve` before
    # `hive swarm run`, which builds the Hive of its own the goal runs in; the packet, started now,
    # dials whichever listener answers on the address its invite named.
    async with running(device):
        ran = await _swarm(manifest, "run", _LABEL, _GOAL, "--timeout", str(_GOAL_TIMEOUT_S))

    assert ran.exit_code == 0, ran.stdout + ran.stderr
    assert "succeeded" in ran.stdout
    await _the_work_was_done_on_the_device_and_nowhere_else(manifest, device, node)


async def _the_work_was_done_on_the_device_and_nowhere_else(
    manifest: Path, device: EnrolledDevice, node: SwarmNode
) -> None:
    """The goal's task was placed on the device's Cell, run there, and the device left as found."""
    # The ceiling named the device's Cell, so the Queen's placement chose nothing else: every task
    # she assigned went to the device's Cell and to the Warden that serves it.
    assigned = await _events(manifest, "task.assigned")
    placed = {(event.payload["cell_id"], event.payload["warden_id"]) for event in assigned}
    assert assigned and placed == {(node.cell_id, node.warden_id)}
    commands = await _events(manifest, "swarm.command_sent")
    assert commands, "no command was ever sent to the device"
    leased = await _events(manifest, "cell.leased")
    lease_ids = {str(e.payload["lease_id"]) for e in leased if e.subject_id == node.cell_id}
    assert {str(event.payload["lease_id"]) for event in commands} <= lease_ids
    # The device was left as found: no lease's scratch directory is still there.
    await wait_until(
        lambda: not any(device.layout.scratch(LeaseId(lease)).exists() for lease in lease_ids),
        timeout_s=_WAIT_S,
    )
