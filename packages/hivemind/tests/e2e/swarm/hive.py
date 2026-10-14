"""Build a Hive with the Swarm on, script its Drone, and read what it recorded about a device.

`swarm_hive` writes the usual `fake_manifest` (every model slot on one scripted `kind = "fake"`
provider) and appends a `[swarm]` section that turns the Swarm on, on loopback with no TLS and a
short beat, then builds the Hive exactly as `hive run` does: one `HaikuScript` answers the Queen's
planning call and every Drone turn. `device_worker_turn` is that Drone: it runs one command on the
device and writes one file there, both through the `PollenSession` the Warden leased, then stops.
The remaining helpers are the reads a scenario makes (which device enrolled, whether its Warden is
attached, what the trail says) and the one write it needs, revoking a node the way the operator's
`hive swarm revoke` will.

Fits into the Hive:
    Test infrastructure (codingrules section 14.5), not shipped. Used by `tests/e2e/
    test_swarm_level0.py`; depends on `hivemind.cli.compose`, `hivemind.swarm` and the e2e kernel
    helpers.

Key invariants:
    - The Hive is the real one: the real Queen, Wardens, listener, registry and door over the
      manifest's own SQLite file. Only the model is scripted.
    - The `[swarm]` beat is short (a quarter of a second) so the gate's reconcile pass, which runs
      at that interval, notices a revocation within moments; nothing else about the manifest is
      tuned.

See Also:
    - tests/e2e/kernel_helpers.py for HaikuScript and the tool-call scripting this builds on.
    - builders.cli for fake_manifest, the manifest this one extends.
"""

from __future__ import annotations

import sys
import uuid
from collections.abc import Sequence
from pathlib import Path

from builders.swarm import swarm_manifest
from e2e.kernel_helpers import (
    HaikuScript,
    single_task_plan,
    text_response,
    tool_response,
    tool_round_count,
    write_call,
)

from hivemind.cell import CellIdentity
from hivemind.cli.compose import Hive, build_hive
from hivemind.llm import LLMRequest, LLMResponse
from hivemind.manifest import load_manifest
from hivemind.pheromone import PheromoneEvent, TrailQuery
from hivemind.swarm import NodeStatus, SwarmNode, SwarmRecorder
from hivemind.wardens import Warden, WardenState
from waggle.clock import SystemClock
from waggle.ids import DeviceId

# The beat of the Hive's reconcile pass and of the packet's heartbeats, in seconds.
BEAT_S = 0.25
REPORT_FILE = "device-report.txt"  # What the scripted Drone writes on the device.
HELLO = "hello from the Hive"  # What the scripted Drone's command prints.
# The scripted command leaves a child behind that outlives it, as a careless command would, with
# this marker in its command line so a test can look for it: only the lease's release ends it. The
# marker is this process's own, so a parallel run's child, or one a failed run left behind, is never
# mistaken for it; the child's sleep is short for the same reason.
STRAY = f"swarm-e2e-stray-{uuid.uuid4().hex}"
_STRAY_LIFETIME_S = 120
_CHILD = f"import time; time.sleep({_STRAY_LIFETIME_S})  # {STRAY}"
_SPAWN_STRAY = (
    "import subprocess, sys; "
    f"subprocess.Popen([sys.executable, '-c', {_CHILD!r}], "
    "stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL); "
    f"print({HELLO!r})"
)

__all__ = [
    "BEAT_S",
    "HELLO",
    "REPORT_FILE",
    "STRAY",
    "attached_warden_ids",
    "device_warden",
    "device_worker_turn",
    "enrolled_node",
    "revoke",
    "swarm_hive",
    "trail_events",
    "warden_is_idle",
]


def swarm_hive(tmp_path: Path, port: int) -> Hive:
    """Build a Hive whose Swarm listens on loopback `port`, its Drone working on a device.

    Args:
        tmp_path: The test's own directory; the manifest, the Hive's database and its scratch live
            under it.
        port: The loopback TCP port the swarm listener binds; a free one.

    Returns:
        A Hive not yet started, with `Hive.swarm` set.
    """
    path = swarm_manifest(tmp_path, listen_port=port, heartbeat_interval_s=BEAT_S)
    script = HaikuScript(device_worker_turn, plan=single_task_plan(REPORT_FILE))
    return build_hive(
        load_manifest(path, {}),
        environ={},
        clock=SystemClock(),
        responders={"fake": script.responder},
    )


def device_worker_turn(request: LLMRequest) -> LLMResponse:
    """The Drone's script: run one command on the device, write one file there, then say so."""
    if tool_round_count(request) > 0:
        return text_response("Ran a command and wrote the report on the device.")
    command = ("run_hello", "run_command", {"argv": [sys.executable, "-c", _SPAWN_STRAY]})
    return tool_response(request, (command, write_call(REPORT_FILE, HELLO)))


async def enrolled_node(hive: Hive) -> SwarmNode:
    """Return the one device enrolled with `hive`'s registry.

    Raises:
        AssertionError: There is not exactly one.
    """
    assert hive.swarm is not None
    [node] = await hive.swarm.registry.nodes()
    return node


def attached_warden_ids(hive: Hive) -> Sequence[str]:
    """Every Warden the Queen has attached right now, the Hive Stand's included."""
    return [link.warden_id for link in hive.queen.wardens]


def device_warden(hive: Hive, node: SwarmNode) -> Warden | None:
    """The Warden the gate serves a device with; None before it is started and after it is gone."""
    assert hive.swarm is not None
    served = hive.swarm.gate.wardens.get(node.device_id)
    return None if served is None else served.warden


def warden_is_idle(hive: Hive, node: SwarmNode) -> bool:
    """Whether the device's Warden is up, in WATCH and holds no lease.

    That is the state a device is left as found in: nothing of the Hive's is on it.
    """
    warden = device_warden(hive, node)
    return warden is not None and warden.state is WardenState.WATCH and warden.lease is None


async def trail_events(hive: Hive, kind: str | None = None) -> list[PheromoneEvent]:
    """Every event on `hive`'s trail (of `kind`, when given), oldest first."""
    return list(await hive.stores.trail.query(TrailQuery(kind=kind)))


async def revoke(hive: Hive, device_id: DeviceId) -> None:
    """Revoke a device the way the operator's `hive swarm revoke` will: status and event together.

    Args:
        hive: The running Hive.
        device_id: The device to revoke.
    """
    assert hive.swarm is not None
    node = await hive.swarm.registry.node(device_id)
    assert node is not None
    identity = CellIdentity(
        hive_id=hive.manifest.hive.id, node_id=hive.manifest.hive.node_id, actor="human"
    )
    recorder = SwarmRecorder(hive.stores.trail, hive.clock, identity)
    event = recorder.swarm_event(
        "swarm.revoked", device_id, {"device_id": device_id, "reason": "revoked by the test"}
    )
    await hive.swarm.registry.set_status(
        device_id, node.status, NodeStatus.REVOKED, hive.clock.now(), event
    )
