"""Test a whole packet colonizing its device: PREPARE, START, a restart that adopts, WITHDRAW.

A real packet runs against the scripted Queen over a real loopback WebSocket. The runtime is a
dummy ``hivemind`` wheel built by hand and installed by a real ``pip`` into a real virtual
environment, and the Warden it starts is the stub of ``support/runtime_kit.py``; nothing of the real
runtime is built or run. The scenario is the one ADR-0052 describes from the device's side: the
Queen orders the three steps, the packet answers each with ``swarm.colonized``, its heartbeats
report the level it is on, a packet that is stopped and started again finds the Warden it left
running, and withdrawing it returns the device to Level 0.

Fits into the Hive:
    End-to-end test of pollen.build, pollen.agent and pollen.bootstrap together (codingrules
    section 14, the exit criteria exercised by a real task where one can).

Key invariants:
    - The Warden is ended before the test returns, whatever happens inside it.

See Also:
    - tests/bootstrap/ for the pieces under test one at a time.
    - docs/adr/0052-runtime-ladder-warden-moves-first.md for the scenario.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from device_kit import Device, make_device, running
from queen_double import QueenDouble, QueenSession
from runtime_kit import STUB_VERSION, build_wheelhouse, kill_group, wait_for_file

from pollen.state import InstallSettings, load_state, save_state
from waggle.clock import Clock
from waggle.envelope import Envelope
from waggle.messages.swarm import (
    Colonize,
    ColonizeAction,
    Colonized,
    ColonizeOutcome,
    DeviceHeartbeat,
    RuntimeLevel,
)

pytestmark = pytest.mark.skipif(
    sys.platform == "win32", reason="the stub Warden is a POSIX script that handles SIGTERM"
)

PREPARE_WAIT_S = 60.0  # A real venv and a real pip: seconds on a fast host, more on a slow one.


def _installed(device: Device, tmp_path: Path) -> Device:
    """Record what ``pollen install`` records: the wheelhouse and the device manifest."""
    manifest = tmp_path / "warden.toml"
    manifest.write_text("[llm]\n", encoding="utf-8")
    install = InstallSettings(
        runtime_wheelhouse=str(build_wheelhouse(tmp_path / "wheels")),
        warden_manifest=str(manifest),
    )
    state = device.state.model_copy(update={"install": install})
    save_state(device.layout.state, state)
    return Device(device.layout, state, device.signer)


async def _order(
    queen: QueenDouble, session: QueenSession, device: Device, action: ColonizeAction
) -> Colonized:
    """Send the Queen's order for ``action`` and return the packet's answer to it."""
    sent = await session.send(
        Colonize(
            cell_id=queen.cell_id,
            device_id=device.state.device_id,
            action=action,
            warden_id=queen.warden_id,
            runtime_version=STUB_VERSION if action is ColonizeAction.PREPARE else None,
            warden_url=queen.url if action is ColonizeAction.START else None,
            deadline_s=PREPARE_WAIT_S,
            reason="The test orders it.",
        )
    )
    answer = await session.expect(
        Colonized, wait_s=PREPARE_WAIT_S, where=lambda e: e.correlation_id == sent.id
    )
    return answer.payload


def _colonized(envelope: Envelope) -> bool:
    """Whether a frame is a heartbeat reporting that a Warden lives on the device."""
    payload = envelope.payload
    return isinstance(payload, DeviceHeartbeat) and payload.level is RuntimeLevel.COLONIZED


def _alive(pid: int) -> bool:
    """Whether a process with this pid is running."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


async def test_a_queen_colonizes_a_device_a_restart_adopts_the_warden_and_withdraw_ends_it(
    queen: QueenDouble, tmp_path: Path, clock: Clock
) -> None:
    device = _installed(make_device(queen, tmp_path / "data", clock), tmp_path)
    pid = 0
    try:
        async with running(device, clock):
            session = await queen.next_session()

            prepared = await _order(queen, session, device, ColonizeAction.PREPARE)
            started = await _order(queen, session, device, ColonizeAction.START)
            pid = started.warden_pid or 0
            await wait_for_file(device.layout.warden_state / "ready")
            heartbeat = await session.expect(DeviceHeartbeat, where=_colonized)

        assert prepared.outcome is ColonizeOutcome.PREPARED
        assert (prepared.level, prepared.runtime_version) == (RuntimeLevel.GATEWAY, STUB_VERSION)
        assert (
            started.outcome is ColonizeOutcome.STARTED and started.level is RuntimeLevel.COLONIZED
        )
        assert pid > 0 and heartbeat.payload.level is RuntimeLevel.COLONIZED
        # The packet has stopped; the Warden it started has not (it is not part of any lease).
        assert _alive(pid)
        assert load_state(device.layout.state).warden is not None

        async with running(device, clock):
            second = await queen.next_session()
            again = await _order(queen, second, device, ColonizeAction.START)
            first_beat = await second.expect(DeviceHeartbeat)  # Reports the adopted level.
            withdrawn = await _order(queen, second, device, ColonizeAction.WITHDRAW)

        assert again.warden_pid == pid  # Adopted: no second Warden.
        assert first_beat.payload.level is RuntimeLevel.COLONIZED
        assert withdrawn.outcome is ColonizeOutcome.WITHDRAWN
        assert withdrawn.level is RuntimeLevel.GATEWAY
        assert (device.layout.warden_state / "stopped").read_text() == "term"
        assert load_state(device.layout.state).warden is None
    finally:
        if pid and _alive(pid):
            kill_group(pid)
