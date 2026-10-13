"""Test the dead-man clock: how long since the Queen was heard, never reset by a restart.

A Pollen Packet (the thin gateway program on a device the Hive borrows) with no Warden on the device
cannot ask anyone what to do when it loses the Queen (the Hive's central orchestrator), so it
releases its leases itself when she has been silent too long (gateway-contract section 13). The
silence is measured on the monotonic clock, so changing the wall clock neither trips the switch nor
delays it, and the last moment she was heard is persisted so that restarting the packet does not
restart the clock. These tests drive a ``FakeClock`` and check each of those promises.

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Tests pollen.lease.deadman.

Key invariants:
    - Silence grows with the monotonic clock only; hearing the Queen resets it to zero.
    - A persisted moment in the future counts as no gap at all, never as negative silence.

See Also:
    - docs/waggle/gateway-contract.md section 13.
"""

from __future__ import annotations

from datetime import timedelta

from pollen.lease import DeadManSwitch
from waggle.clock import FakeClock


def test_silence_grows_with_the_clock_and_hearing_the_queen_resets_it() -> None:
    clock = FakeClock()
    switch = DeadManSwitch(clock)
    clock.advance(30)
    assert switch.silence_s() == 30
    switch.heard()
    assert switch.silence_s() == 0
    clock.advance(5)
    assert switch.silence_s() == 5


def test_the_switch_trips_only_past_the_limit() -> None:
    clock = FakeClock()
    switch = DeadManSwitch(clock)
    clock.advance(120)
    assert not switch.is_tripped(120)
    clock.advance(0.5)
    assert switch.is_tripped(120)


def test_a_restart_continues_the_silence_from_the_persisted_moment() -> None:
    clock = FakeClock()
    heard_at = clock.now() - timedelta(seconds=90)
    switch = DeadManSwitch(clock, last_heard_at=heard_at)
    assert switch.silence_s() == 90
    clock.advance(31)
    assert switch.is_tripped(120)


def test_a_persisted_moment_ahead_of_the_wall_clock_counts_as_no_gap() -> None:
    clock = FakeClock()
    switch = DeadManSwitch(clock, last_heard_at=clock.now() + timedelta(hours=1))
    assert switch.silence_s() == 0


def test_the_moment_to_persist_is_the_wall_clock_minus_the_silence() -> None:
    clock = FakeClock()
    switch = DeadManSwitch(clock)
    clock.advance(10)
    assert switch.heard_at() == clock.now() - timedelta(seconds=10)
    switch.heard()
    assert switch.heard_at() == clock.now()
