"""Integration test: the desktop-ubuntu image equips a task with a whole Exoskeleton (roadmap 6.1).

`@pytest.mark.integration` (codingrules 14.2). Skips cleanly when no Docker daemon answers or
`hivemind/desktop-ubuntu:dev` is not built (images/desktop-ubuntu/README.md). One container runs
`_IN_CELL` as the image's own unprivileged `hive` user, through the image's own interpreter and
only shipped code, the way the in-Cell Warden would: it probes the Cell as the Warden announces
it, opens a lease through `InCellSpawnSource`, attaches a desktop with audio and a browser, uses
every peripheral once, detaches, and prints one JSON report this test reads. The image README's
promise is what is asserted: the probe reports `can_start_display`, `has_audio` and
`has_browser` with nothing running at boot, every peripheral works for the unprivileged user, and
detach leaves no lease-started process behind.

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Exercises images/desktop-ubuntu with
    hivemind.exoskeleton.attach, its X11/PulseAudio backends and the Chromium fast path, end to
    end inside a real container.

Key invariants:
    - None: this module holds tests only.

See Also:
    - images/desktop-ubuntu/README.md for what the image installs and promises.
    - packages/hivemind/tests/contracts/test_exoskeleton_contract.py for the same peripherals on
      the host.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from integration.docker_helpers import daemon_reachable, image_present

pytestmark = pytest.mark.integration

IMAGE = "hivemind/desktop-ubuntu:dev"
_RUN_TIMEOUT_S = 300  # A cold container, Xvfb, PulseAudio and Chromium starting once each.

# Runs inside the container as `hive`. Only shipped code: the image has no test builders. Short
# scratch paths, because PulseAudio's socket must fit a Unix socket address.
_IN_CELL = r"""
import asyncio, json
from pathlib import Path

from hivemind.cell import AccessLevel, CellIdentity, CombShieldLevel, LeaseRequest
from hivemind.cell.local import HiveStandConfig
from hivemind.cell.local.probe import probe_host
from hivemind.exoskeleton import AttachDeps, ExoskeletonConfig, Point, attach
from hivemind.exoskeleton.browser import ChromiumLauncher
from hivemind.guard import CapabilitySet
from hivemind.pheromone import MemoryPheromoneTrail, TrailQuery
from hivemind.wardens.spawn.in_cell import InCellSpawnConfig, InCellSpawnSource
from waggle.clock import SystemClock
from waggle.ids import new_cell_id, new_hive_id, new_node_id, new_warden_id
from waggle.messages.capping import MouseButton
from waggle.messages.task import ExoskeletonNeed

GRANTS = ("exoskeleton:display", "exoskeleton:audio", "exoskeleton:browser")


async def main():
    clock = SystemClock()
    root = Path("/tmp/hm")
    probed = probe_host(HiveStandConfig(
        enabled=True, scratch_root=root, scratch_quota_mb=1, disk_reserve_mb=0,
        max_sub_bees=None, cores=None, memory_bytes=None, access_level=AccessLevel.FULL,
    ))
    caps = probed.capabilities
    identity = CellIdentity(hive_id=new_hive_id(clock), node_id=new_node_id(clock), actor="system")
    trail = MemoryPheromoneTrail(clock)
    config = InCellSpawnConfig(
        cell_id=new_cell_id(clock), capabilities=caps, capacity=probed.capacity,
        comb_shield=CombShieldLevel.MEADOW, scratch_root=root,
    )
    source = InCellSpawnSource(config, identity, trail, clock)
    cell = (await source.cells())[0]
    lease = await source.lease(LeaseRequest(
        cell_id=cell.id, holder=new_warden_id(clock), access_level=AccessLevel.FULL,
    ))
    session = await source.open_session(lease)
    deps = AttachDeps(
        trail=trail, identity=identity, clock=clock,
        config=ExoskeletonConfig(browser_sandbox=False), browser_launcher=ChromiumLauncher(clock),
    )
    handle = await attach(
        cell, session, ExoskeletonNeed(audio=True), CapabilitySet.parse(*GRANTS), deps
    )
    kit = handle.peripherals
    frame = await kit.compound_eye.capture()
    await kit.antennae.click(Point(10, 10), MouseButton.LEFT)
    heard = await kit.buzz.listen(0.5)
    page = lease.scratch_root / "page.html"
    page.write_text("<title>hive fixture</title><h1>Hello</h1>")
    await kit.browser.navigate(page.as_uri())
    title = await kit.browser.title()
    detached = await handle.detach()
    kinds = [event.kind for event in await trail.query(TrailQuery())]
    print(json.dumps({
        "capabilities": {
            "has_display": caps.has_display, "can_start_display": caps.can_start_display,
            "has_audio": caps.has_audio, "has_browser": caps.has_browser,
        },
        "frame": [frame.width, frame.height, len(frame.png)],
        "heard_s": heard.duration_s,
        "title": title,
        "detach": {"stopped": detached.stopped, "still_running": detached.still_running},
        "trail": kinds,
    }))


asyncio.run(main())
"""


def _skip_unless_image() -> None:
    if not daemon_reachable():
        pytest.skip("no Docker daemon reachable")
    if not image_present(IMAGE):
        pytest.skip(f"{IMAGE} is not built locally; see images/desktop-ubuntu/README.md")


def _run_in_cell() -> dict[str, Any]:
    """Run `_IN_CELL` in a fresh container as its default user; return the JSON it printed."""
    import docker  # The docker extra; daemon_reachable already proved it imports.
    from docker.errors import ContainerError

    # The image's own entrypoint is the in-Cell Warden, so the interpreter is named instead;
    # `remove=True` leaves no container behind whatever happens inside.
    try:
        output = docker.from_env(timeout=_RUN_TIMEOUT_S).containers.run(
            IMAGE,
            ["-c", _IN_CELL],
            entrypoint="/opt/hivemind/venv/bin/python",
            remove=True,
            stdout=True,
            stderr=False,
        )
    except ContainerError as error:
        pytest.fail(f"the in-Cell script failed: {str(error.stderr)[-4000:]}")
    report: dict[str, Any] = json.loads(output.decode().strip().splitlines()[-1])
    return report


def test_the_desktop_image_equips_a_task_and_leaves_nothing_running() -> None:
    _skip_unless_image()

    report = _run_in_cell()

    # The probe, as the in-Cell Warden announces the Cell: nothing runs at boot.
    assert report["capabilities"] == {
        "has_display": False,
        "can_start_display": True,
        "has_audio": True,
        "has_browser": True,
    }
    # Every peripheral works as the unprivileged `hive` user.
    width, height, png_bytes = report["frame"]
    assert (width, height) == (1280, 800) and png_bytes > 0
    assert report["heard_s"] > 0
    assert report["title"] == "hive fixture"
    # Detach stops exactly what attach started, and says so on the trail.
    assert report["detach"]["still_running"] == 0
    assert report["detach"]["stopped"] >= 3  # Xvfb, openbox, PulseAudio; Chromium besides.
    assert "cell.exoskeleton_attached" in report["trail"]
    assert "cell.exoskeleton_detached" in report["trail"]
