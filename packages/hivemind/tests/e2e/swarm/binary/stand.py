"""Build a whole Hive whose swarm listener a container can dial, and script the Drone it runs.

The swarm tests so far give a container a Stand with no Queen (`e2e.swarm.start_stand`), or run
the packet in the test's own process against a Hive on loopback (`e2e.swarm.hive`). A scenario
that puts a task on a packet in a container needs both halves at once: the real Queen, gate and
Warden, and a listener on the container network's host address that serves `wss://` with a
self-signed certificate the invite pins. `bridge_hive` builds that Hive from a manifest with
`[swarm]` pointing at the bridge, exactly as `hive serve` would read it. `DeviceDrone` is the
model's side: one `run_command` that leaves a report of the machine it ran on in a file, one
`write_file`, and a `read_file` of the report, all three carried out through the `PollenSession`
the device's Warden leased. A command's own output never reaches the model (the Capping gate
answers with its verdict), so the report file is how the Drone, and through it the test, learns
where the command ran; the Drone keeps what it read.

Fits into the Hive:
    Test infrastructure (codingrules section 14.2), not shipped. Used by the phase 11 packaging
    scenario; builds on `builders.swarm` (manifest, free port), `e2e.kernel_helpers` (the scripted
    model) and `e2e.swarm.tls` (the certificate), and `hivemind.cli.compose` for the Hive itself.

Key invariants:
    - The Hive is the real one (Queen, swarm gate, device Wardens, listener, SQLite registry);
      only the model is scripted.
    - The command runs no program the clean Debian image lacks: it is `sh`, what the base system
      ships and the packet's own binary, because the device has no Python for a script to use.
    - The goal's ceiling names the device's Cell and no other, so its one task can be placed
      nowhere else.
    - The Drone costs the device no cores in this manifest, and only in it: a container reads the
      load of the whole machine, and a busy one would otherwise keep the task waiting for room.

See Also:
    - tests/e2e/test_swarm_level0.py for the same task on a packet in the test's own process.
    - e2e/swarm/hive.py for the loopback Hive this one is the container-facing counterpart of.
"""

from __future__ import annotations

from pathlib import Path

from builders.swarm import free_port, swarm_manifest
from e2e.kernel_helpers import (
    HaikuScript,
    single_task_plan,
    text_response,
    tool_response,
    tool_round_count,
    wait_until,
    write_call,
)
from e2e.swarm.container import Network
from e2e.swarm.enrolled import HIVE_HOSTNAME
from e2e.swarm.tls import certificate_for

from hivemind.brood_chamber import Task, TaskFilter, is_terminal
from hivemind.cell import HoneyClearance
from hivemind.cli.compose import Hive, build_hive
from hivemind.llm import LLMRequest, LLMResponse, TextPart, ToolResultPart
from hivemind.manifest import load_manifest
from hivemind.queen.swarm_gate import SwarmGate
from hivemind.swarm import SwarmNode
from hivemind.wardens import Warden, WardenState
from waggle.clock import SystemClock

BEAT_S = 0.5  # The packet's heartbeat and the gate's reconcile pass, in seconds.
GOAL = "write a report on the borrowed device"
REPORT_FILE = "device-report.txt"  # What the Drone writes in the lease's scratch directory.
HELLO = "hello from the Hive"
INSPECT_FILE = "inspect.txt"  # What the Drone's command leaves in scratch, and the Drone reads.
# What the Drone's one command reports: the device's own operating system and name, what the
# packet's loader left in the environment of a program it started, and what the packet itself
# says when a command in the lease runs it (a single-file program started by a single-file
# program: the case a half-cleaned environment breaks).
INSPECT = (
    ". /etc/os-release; "
    'echo "os=$PRETTY_NAME"; echo "host=$(hostname)"; echo "loader=[$LD_LIBRARY_PATH]"; '
    'echo "nested=$(pollen version 2>&1)"'
)
_ACTED_AND_READ = 3  # Tool results seen once the first round's two calls and the read are done.

__all__ = [
    "BEAT_S",
    "GOAL",
    "HELLO",
    "INSPECT",
    "INSPECT_FILE",
    "REPORT_FILE",
    "DeviceDrone",
    "bridge_hive",
    "bridge_manifest",
    "idle_warden",
    "run_goal_on",
]


class DeviceDrone:
    """The scripted Drone: a command and a file on the device, then a read of the report."""

    def __init__(self) -> None:
        """Start with nothing shown; `printed` is set when the model sees the report it read."""
        self.printed = ""

    def turn(self, request: LLMRequest) -> LLMResponse:
        """Answer one WORKER-slot call: act, read the command's report back, then say so."""
        rounds = tool_round_count(request)
        if rounds == 0:
            # The command's output goes to a file in scratch (its working directory) because the
            # model is never shown a command's output, only the Capping gate's verdict on it.
            report = f"( {INSPECT} ) > {INSPECT_FILE}"
            command = ("inspect_device", "run_command", {"argv": ["sh", "-c", report]})
            return tool_response(request, (command, write_call(REPORT_FILE, HELLO)))
        if rounds < _ACTED_AND_READ:
            read = ("read_inspection", "read_file", {"path": INSPECT_FILE})
            return tool_response(request, (read,))
        self.printed = _tool_results(request)
        return text_response("Ran a command, wrote the report and read the inspection back.")


def _tool_results(request: LLMRequest) -> str:
    """Everything the tools returned in this attempt, native results and prompted text alike."""
    texts = []
    for message in request.messages:
        for part in message.parts:
            if isinstance(part, ToolResultPart):
                texts.append(part.content)
            elif isinstance(part, TextPart) and "Result for " in part.text:
                texts.append(part.text)
    return "\n".join(texts)


def bridge_manifest(tmp_path: Path, host: str) -> Path:
    """Write the manifest of a Hive whose swarm listener binds `host` and serves `wss://`.

    Args:
        tmp_path: The test's own directory; the manifest, the certificate, the Hive's database
            and its scratch live under it.
        host: The address to bind: the container network's host address. The invite tells a
            device to dial `e2e.swarm.HIVE_HOSTNAME`, which the device's container maps there.

    Returns:
        The manifest's path. Its listener is on a port that was free a moment ago.
    """
    port = free_port(host)
    material = certificate_for(tmp_path, HIVE_HOSTNAME)
    path = swarm_manifest(
        tmp_path,
        listen_host=host,
        listen_port=port,
        advertise_url=f"wss://{HIVE_HOSTNAME}:{port}",
        tls_cert=str(material.cert_path),
        tls_key=str(material.key_path),
        heartbeat_interval_s=BEAT_S,
    )
    _make_the_drone_cost_no_cores(path)
    return path


def _make_the_drone_cost_no_cores(manifest: Path) -> None:
    """Give the Drone a footprint of no cores, so the host's load cannot hold its task back.

    A device's free cores are its cores less the one-minute load, which a container reads from the
    machine it runs on, and a Drone that cannot fit finds no room and waits (a deferred
    `forage.denied`, up to `zero_grant_patience_s`). The default half-core Drone waited out a busy
    host, and this scenario is about the packet on a clean machine, not about scheduling, which
    `test_zero_grant.py` covers: a role that costs no cores is bound by no load.
    """
    text = manifest.read_text(encoding="utf-8")
    heavy = "[forage.roles.drone]\ncpu_cores = 0.5\n"
    if heavy not in text:
        raise AssertionError("builders.cli.fake_manifest no longer writes the Drone's footprint.")
    manifest.write_text(text.replace(heavy, "[forage.roles.drone]\ncpu_cores = 0.0\n"), "utf-8")


def bridge_hive(tmp_path: Path, network: Network, drone: DeviceDrone) -> Hive:
    """Build a Hive whose swarm listener binds `network`'s host address over `wss://`.

    Args:
        tmp_path: The test's own directory (see `bridge_manifest`).
        network: The container network; its host address is where the listener binds.
        drone: The scripted Drone every WORKER call is answered by.

    Returns:
        A Hive not yet started, with `Hive.swarm` set. Build it outside any event loop, as
        `build_hive` does its own small `asyncio.run`.
    """
    manifest = load_manifest(bridge_manifest(tmp_path, network.gateway), {})
    script = HaikuScript(drone.turn, plan=single_task_plan(REPORT_FILE))
    return build_hive(
        manifest, environ={}, clock=SystemClock(), responders={"fake": script.responder}
    )


def idle_warden(gate: SwarmGate, node: SwarmNode) -> Warden | None:
    """The device's Warden once it is started and holds no lease; None before."""
    served = gate.wardens.get(node.device_id)
    if served is None or served.warden.lease is not None:
        return None
    return served.warden if served.warden.state is WardenState.WATCH else None


async def run_goal_on(hive: Hive, node: SwarmNode, timeout_s: float) -> Task:
    """Submit a goal that can be placed only on `node`'s Cell, and wait for its one task to end.

    Args:
        hive: The running Hive.
        node: The device whose Cell is the only one the goal's ceiling allows.
        timeout_s: Real seconds to wait for the task to reach a terminal status.

    Returns:
        The goal's one task, terminal.

    Raises:
        AssertionError: The task was not terminal within `timeout_s`.
    """
    # A ceiling that names the device's Cell and not the Hive Stand's `cell:hive_stand`, so the
    # Queen's placement can choose nothing else. The rest is what the scripted Drone needs.
    ceiling = (
        f"cell:real:{node.cell_id}",
        "cell:comb_shield:*",
        "llm:*",
        "tool:*",
        "fs:read:**",
        "fs:write:**",
        "exec:*",
        "question:human",
    )
    goal_id = await hive.queen.submit_goal(GOAL, clearance=HoneyClearance.C1, capabilities=ceiling)
    filter_ = TaskFilter(goal_id=goal_id)

    async def finished() -> bool:
        tasks = await hive.stores.chamber.list(filter_)
        return bool(tasks) and all(is_terminal(t.status) for t in tasks)

    await wait_until(finished, timeout_s=timeout_s)
    [task] = await hive.stores.chamber.list(filter_)
    return task
