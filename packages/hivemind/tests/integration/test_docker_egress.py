"""Integration: isolating a real Docker Cell cuts its egress, keeps its link and taints its store.

Roadmap step 10.6a, proved on a real daemon. A whole Hive runs with `[virtual_cells] backend =
"docker"` and a control subnet: every Cell is dual-homed on the Hive's internal control network
(where the Queen's listener binds the gateway) and its own egress network. A goal the Hive Stand
may not take lands on a real container, whose Drone runs a command round after round. A Handoff is
checkpointed inside the Cell; then a Guard request under the shipped dire pattern, filed through
the Queen's real door, isolates the Cell. From inside the container: an outside host is reachable
before the cut and not after it, and neither is the host's docker0 (where the model server
answers), while the control gateway's listener still is. The Waggle link lives on: heartbeats keep
arriving, no CELL_UNREACHABLE, and the Warden never re-attaches. The Cell's own Warden taints the
store it keeps (its `memory.tainted` rows, caused by `cell.isolated`, reach the Queen's trail),
the human's lift restores the egress, and a resume from the tainted Handoff is refused inside the
Cell and the task held. At teardown the container is gone.

`@pytest.mark.integration` (codingrules 14.2): skips cleanly when no daemon answers, when the Cell
image is not built (`HIVEMIND_TEST_CELL_IMAGE`, default `integration.docker_helpers.IMAGE`; build
it from this tree, since the Cell must carry this step's in-Cell Warden), or when no outside host
is reachable from a container on this runner at all.

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Exercises hive/backends/docker (the control
    network and the egress lever), queen/isolation and wardens/isolation end to end.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.hive.backends.docker.network for the dual-homing design.
    - docs/guard/isolation.md, "Egress on the backends".
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from builders.isolation import DIRE_RULE, make_guard_report
from e2e.kernel_helpers import wait_until
from e2e.scripted_openai import Scenario, free_port, serve_in_thread, write_manifest
from integration.docker_helpers import ALL_INTERFACES, IMAGE, daemon_reachable, image_present

from hivemind.brood_chamber import Task, TaskFilter, TaskStatus
from hivemind.cell import HoneyClearance
from hivemind.cli.compose import Hive, build_hive, run_hive
from hivemind.hive.backends.docker.backend import container_name
from hivemind.manifest import load_manifest
from hivemind.pheromone import PheromoneEvent, TrailQuery
from hivemind.queen.dispatcher import resume_paused
from hivemind.supervision import AlarmKind
from waggle.clock import SystemClock
from waggle.envelope import wrap
from waggle.ids import CellId, new_device_id, new_hive_id, new_node_id
from waggle.messages import HandoffRef
from waggle.messages.labels import HoneyClearance as WireHoneyClearance
from waggle.messages.supervision import Intervene, InterventionAction

if TYPE_CHECKING:
    # Only for the annotations: the SDK is an optional extra, imported for real in the test.
    from docker import DockerClient

pytestmark = pytest.mark.integration
log = logging.getLogger(__name__)  # What the run saw, for `-o log_cli=true`: the proof's record.

_IMAGE = os.environ.get("HIVEMIND_TEST_CELL_IMAGE", IMAGE)
_OUTSIDE = "archive.ubuntu.com"  # Plain http; reachable from a container here before the cut.
_DOCKER0 = "172.17.0.1"  # The host's docker0, where host.docker.internal (host-gateway) points.
_WAIT_S = 120.0  # A cold container, a Python boot, a trail segment on a 15 s cadence.
_HEARTBEAT_WAIT_S = 45.0  # The in-Cell Warden beats every 15 s; three beats is plenty.
_VIRTUAL_ONLY = ("cell:virtual", "cell:comb_shield:*", "llm:*", "tool:*", "fs:read:**")
_VIRTUAL_ONLY_TOOLS = (*_VIRTUAL_ONLY, "fs:write:**", "exec:*", "question:human")
_TAINTED_HANDOFF = "guard.scope.tainted_handoff"
# Run inside the Cell: resolve the outside host, then try each "label,host,port" target.
_PROBE = """
import json, socket, sys
out = {}
try:
    out["resolved"] = socket.gethostbyname(sys.argv[1])
except OSError as exc:
    out["resolved"] = None
for spec in sys.argv[2:]:
    label, host, port = spec.split(",")
    try:
        socket.create_connection((host, int(port)), timeout=4).close()
        out[label] = "open"
    except OSError as exc:
        out[label] = f"{type(exc).__name__}({exc.errno})"
print(json.dumps(out))
"""


@dataclass(frozen=True)
class _Rig:
    """What the scenario needs besides the Hive: the control network and the ports it probes."""

    subnet: str
    gateway: str
    listen_port: int
    model_port: int


def test_isolating_a_docker_cell_cuts_egress_keeps_the_link_and_taints_its_store(
    tmp_path: Path,
) -> None:
    if not daemon_reachable():
        pytest.skip("no Docker daemon reachable")
    if not image_present(_IMAGE):
        pytest.skip(f"{_IMAGE} is not built locally; build it from this tree")
    import docker

    client = docker.from_env()
    scenario = Scenario(command=("sleep", "3"))
    clock = SystemClock()
    hive_id, node_id = new_hive_id(clock), new_node_id(clock)
    try:
        with serve_in_thread(scenario, host=ALL_INTERFACES) as base_url:
            rig = _rig(client, int(base_url.rsplit(":", 1)[1].split("/")[0]))
            path = _manifest(tmp_path, base_url, (hive_id, node_id), rig)
            hive = build_hive(load_manifest(path, {}), environ={}, clock=clock)
            asyncio.run(_scenario(hive, client, rig))
        # Teardown destroyed the Cell: nothing of this Hive's is left running.
        assert (
            client.containers.list(all=True, filters={"label": f"hivemind.hive_id={hive_id}"}) == []
        )
    finally:
        _sweep(client, hive_id)
        client.close()


async def _scenario(hive: Hive, client: DockerClient, rig: _Rig) -> None:
    """Run the Hive; isolate its busy Docker Cell; probe from inside; lift; resume; tear down."""
    async with run_hive(hive):
        task, cell_id = await _a_busy_docker_cell(hive)
        container = container_name(cell_id)
        before = await _probe(client, container, rig, resolved=None)
        if before.get("http_ip") != "open":
            pytest.skip(f"{_OUTSIDE} is not reachable from a container on this runner: {before}")
        checkpoint = await _checkpoint(hive, task)
        isolated, cut_at = await _isolate(hive, task, cell_id)
        cut = await _probe(client, container, rig, resolved=str(before["resolved"]))
        tainted = await _in_cell_labels(hive, isolated.id)
        await _link_lived_through(hive, task, cut_at)
        await hive.queen.lift_isolation(cell_id, new_device_id(hive.clock))
        lifted = await _probe(client, container, rig, resolved=str(before["resolved"]))
        refusal = await _resume_is_refused(hive, task, checkpoint)
        log.info("before cut: %s | after cut: %s | after lift: %s", before, cut, lifted)
        log.info(
            "cell.isolated egress=%s; in-Cell labels=%d", isolated.payload["egress"], len(tainted)
        )
        log.info("resume refused inside the Cell: %s", refusal.payload["rule"])
    assert (before["http_ip"], before["model"], before["listener"]) == ("open", "open", "open")
    assert isolated.payload["egress"] == "cut"
    assert cut["listener"] == "open"  # The link's own address: the control gateway.
    assert cut["http_ip"] != "open" and cut["model"] != "open" and cut["resolved"] is None
    assert (lifted["http_ip"], lifted["model"]) == ("open", "open")
    assert tainted and refusal.payload["rule"] == _TAINTED_HANDOFF


async def _a_busy_docker_cell(hive: Hive) -> tuple[Task, CellId]:
    """Submit a Virtual-only goal and wait until its Drone is running commands in a container."""
    goal_id = await hive.queen.submit_goal(
        "keep busy", clearance=HoneyClearance.C1, capabilities=_VIRTUAL_ONLY_TOOLS
    )

    async def running() -> bool:
        tasks = await hive.stores.chamber.list(TaskFilter(goal_id=goal_id))
        return (
            bool(tasks) and tasks[0].status is TaskStatus.RUNNING and tasks[0].cell_id is not None
        )

    await wait_until(running, timeout_s=_WAIT_S)
    [task] = await hive.stores.chamber.list(TaskFilter(goal_id=goal_id))
    assert task.cell_id is not None
    return task, CellId(task.cell_id)


async def _checkpoint(hive: Hive, task: Task) -> HandoffRef:
    """Have the Drone checkpoint a Handoff in its Cell, and wait for the Cell to ship the row."""
    [link] = [link for link in hive.queen.wardens if link.warden_id == task.warden_id]
    order = Intervene(
        action=InterventionAction.CHECKPOINT,
        subject=None,
        task_id=task.id,
        slot=None,
        alarm_id=None,
        reason="Checkpoint before the isolation proof.",
    )
    await link.transport.send(wrap(order, link.hop, clock=hive.clock))
    await wait_until(lambda: _any(hive, "memory.checkpoint", task.id), timeout_s=_WAIT_S)
    [event, *_] = await _events(hive, "memory.checkpoint", task.id)
    return HandoffRef(event_id=event.id, written_at=event.at, clearance=WireHoneyClearance.C1)


async def _isolate(hive: Hive, task: Task, cell_id: CellId) -> tuple[PheromoneEvent, datetime]:
    """File a dire-pattern request through the Queen's door; wait until she has carried it out."""
    [assigned] = await _events(hive, "queen.assigned", task.id)
    report = make_guard_report(
        hive.clock, cell_id=cell_id, rule=DIRE_RULE, event_ids=(assigned.id,), task_ids=(task.id,)
    )
    await hive.guard_door.file_guard_request(report)

    async def decided() -> bool:
        row = await hive.queen._deps.guard.requests.get(report.id)
        return row is not None and row.decision is not None

    await wait_until(decided, timeout_s=_WAIT_S)
    [isolated] = await _events(hive, "cell.isolated", cell_id)
    return isolated, isolated.at


async def _in_cell_labels(hive: Hive, cause: str) -> list[PheromoneEvent]:
    """Wait for the Cell's own Warden's `memory.tainted` rows (its node, caused by `cause`)."""
    own_node = hive.manifest.hive.node_id

    async def labels() -> list[PheromoneEvent]:
        rows = await _events(hive, "memory.tainted")
        return [
            e for e in rows if e.node_id != own_node and e.payload.get("cause_event_id") == cause
        ]

    async def labelled() -> bool:
        return bool(await labels())

    await wait_until(labelled, timeout_s=_WAIT_S)
    return await labels()


async def _link_lived_through(hive: Hive, task: Task, cut_at: datetime) -> None:
    """The Warden's heartbeats keep arriving after the cut, with no Alarm and no re-attach."""
    assert task.warden_id is not None
    warden_id = task.warden_id

    async def beat_after_cut() -> bool:
        liveness = hive.queen._liveness.get(warden_id)
        seen = liveness.last_heartbeat_at if liveness is not None else None
        return seen is not None and seen > cut_at

    await wait_until(beat_after_cut, timeout_s=_HEARTBEAT_WAIT_S)
    liveness = hive.queen._liveness[warden_id]
    assert not liveness.is_offline and liveness.missed_heartbeats == 0
    unreachable = [a for a in hive.queen.human_inbox.alarms if a.kind is AlarmKind.CELL_UNREACHABLE]
    assert unreachable == []
    # One attach for the Cell's whole life: its link never dropped, so it never redialled.
    assert len(await _events(hive, "warden.spawned", warden_id)) == 1


async def _resume_is_refused(hive: Hive, task: Task, checkpoint: HandoffRef) -> PheromoneEvent:
    """Resume the paused task from its tainted Handoff: refused in the Cell, the task held."""
    deps = hive.queen._deps
    await resume_paused(deps, hive.queen.wardens, task.id, checkpoint, "The proof's resume.")

    async def refused() -> list[PheromoneEvent]:
        rows = await _events(hive, "guard.denied")
        return [e for e in rows if e.payload.get("rule") == _TAINTED_HANDOFF]

    async def was_refused() -> bool:
        return bool(await refused())

    await wait_until(was_refused, timeout_s=_WAIT_S)

    async def held() -> bool:
        return (await hive.stores.chamber.get(task.id)).status is TaskStatus.PAUSED

    await wait_until(held, timeout_s=_WAIT_S)
    return (await refused())[0]


async def _probe(
    client: DockerClient, container: str, rig: _Rig, *, resolved: str | None
) -> dict[str, str | None]:
    """Run the probe inside the container, off the event loop the Hive's loops share."""
    targets = [
        f"listener,{rig.gateway},{rig.listen_port}",
        f"model,{_DOCKER0},{rig.model_port}",
    ]
    if resolved is not None:
        targets.append(f"http_ip,{resolved},80")

    def run() -> dict[str, str | None]:
        found = client.containers.get(container)
        result = found.exec_run(["python3", "-c", _PROBE, _OUTSIDE, *targets])
        return dict(json.loads(result.output.decode().strip().splitlines()[-1]))

    report = await asyncio.to_thread(run)
    if resolved is None and report["resolved"]:
        # The first probe names the address every later one tries, DNS or no DNS.
        report = await _probe(client, container, rig, resolved=report["resolved"])
    return report


async def _events(hive: Hive, kind: str, subject_id: str | None = None) -> list[PheromoneEvent]:
    """Every `kind` event (about `subject_id`, when given) on the Queen's trail, oldest first."""
    return list(await hive.stores.trail.query(TrailQuery(kind=kind, subject_id=subject_id)))


async def _any(hive: Hive, kind: str, subject_id: str) -> bool:
    """Whether any `kind` event about `subject_id` is on the Queen's trail yet."""
    return bool(await _events(hive, kind, subject_id))


def _rig(client: DockerClient, model_port: int) -> _Rig:
    """Pick a private /24 no Docker network uses, and a port for the Queen's listener."""
    used = [
        ipaddress.ip_network(pool["Subnet"])
        for network in client.networks.list()
        for pool in (network.attrs.get("IPAM") or {}).get("Config") or []
        if "Subnet" in pool
    ]
    for third in range(1, 255):
        candidate = ipaddress.ip_network(f"10.213.{third}.0/24")
        if not any(candidate.overlaps(taken) for taken in used):
            gateway = str(next(candidate.hosts()))
            return _Rig(str(candidate), gateway, free_port(), model_port)
    raise AssertionError("No free 10.213.x.0/24 for the control network.")


def _manifest(tmp_path: Path, base_url: str, ids: tuple[str, str], rig: _Rig) -> Path:
    """The scripted Hive's manifest, with a Docker Virtual side on the rig's control network."""
    path = write_manifest(tmp_path, base_url, ids)
    extra = f"""
[virtual_cells]
backend = "docker"
default_image = "{_IMAGE}"
network_policy = "egress_only"
ready_timeout_s = {_WAIT_S}
max_cells = 1
listen_host = "{rig.gateway}"
listen_port = {rig.listen_port}
control_subnet = "{rig.subnet}"

[virtual_cells.overwinter]
enabled = false

[placement]
prefer = "virtual"
"""
    path.write_text(path.read_text(encoding="utf-8") + extra, encoding="utf-8")
    return path


def _sweep(client: DockerClient, hive_id: str) -> None:
    """Remove anything of this Hive's the run left behind: containers, volumes, networks."""
    label = {"label": f"hivemind.hive_id={hive_id}"}
    for found in client.containers.list(all=True, filters=label):
        found.remove(force=True)
    for volume in client.volumes.list(filters=label):
        volume.remove(force=True)
    for network in client.networks.list(filters=label):
        network.remove()
