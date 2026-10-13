"""Test the composition root: the packet is assembled from an enrolled data directory, and only so.

``build_packet`` is the one place the Pollen Packet's parts are wired together (the state and key,
the lease table, the outbox, the courier, the four executors, the lease and control services, the
dispatcher, the timers and the loop). It reads files and builds objects and does no network or
process work, so it is cheap to call and refuses cleanly when the data directory holds no
enrolment or a damaged file.

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Tests pollen.build.

Key invariants:
    - Building never dials and never starts a process.
    - A missing enrolment is ``NotEnrolledError``; a damaged file is ``StateError``.

See Also:
    - pollen.agent.packet for the loop that is built.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from device_kit import Device

from pollen import PollenPacket, build_packet
from pollen.errors import NotEnrolledError, StateError
from pollen.platform import select_shim
from pollen.state import DataLayout
from waggle.clock import Clock


def test_an_enrolled_directory_builds_a_packet(device: Device, clock: Clock) -> None:
    packet = build_packet(device.layout, select_shim(), clock)
    assert isinstance(packet, PollenPacket)


def test_an_empty_directory_is_not_enrolled(tmp_path: Path, clock: Clock) -> None:
    with pytest.raises(NotEnrolledError):
        build_packet(DataLayout(tmp_path), select_shim(), clock)


def test_a_state_without_a_key_is_not_enrolled(device: Device, clock: Clock) -> None:
    device.layout.key.unlink()
    with pytest.raises(NotEnrolledError):
        build_packet(device.layout, select_shim(), clock)


def test_a_damaged_lease_table_or_outbox_is_a_state_error(device: Device, clock: Clock) -> None:
    device.layout.lease_table.write_text("not json")
    with pytest.raises(StateError):
        build_packet(device.layout, select_shim(), clock)
    device.layout.lease_table.unlink()
    device.layout.outbox.write_text("garbage\nmore garbage\n")  # Damage before the last line.
    with pytest.raises(StateError):
        build_packet(device.layout, select_shim(), clock)
