"""Tests for waggle.transport.limits: a listener's limits, their limiters and their refusals.

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Exercises ServerLimits, ServerLimiter and
    ConnectionLimiter with a FakeClock, the conftest's recording callback and no network: the
    defaults and the switches, the ceiling's refusal and the node it names, the replay refusal and
    the ids it carries, one replay window shared by all connections against one bucket per
    connection, a missing callback, and a recorder that hangs. What the WebSocket transport does
    with the answers (the 4029 close, the skipped duplicate) is test_websocket_server_limits.py.

Key invariants:
    - None: this module holds tests only.

See Also:
    - waggle.transport.limits for the module under test.
    - conftest.py for the ``refusals`` and ``on_refusal`` fixtures.
"""

from __future__ import annotations

import asyncio
import dataclasses
from collections.abc import Callable

import pytest

from waggle.clock import FakeClock
from waggle.envelope import Envelope
from waggle.ids import new_node_id
from waggle.transport import limits as limits_module
from waggle.transport.ceiling import CeilingLimits
from waggle.transport.limits import (
    ConnectionRefusal,
    RefusalHandler,
    RefusalKind,
    ServerLimiter,
    ServerLimits,
)
from waggle.transport.replay import ReplayLimits

MakeEnvelope = Callable[..., Envelope]

BURST = 2  # A tiny bucket: two frames, the third is over the ceiling.
TINY_TIMEOUT_S = 0.05  # A report timeout short enough that a hung recorder trips it at once.
CEILING = CeilingLimits(max_frames_per_minute=60, burst=BURST)


def test_the_default_limits_switch_both_checks_on_and_none_switches_each_off() -> None:
    assert ServerLimits().replay == ReplayLimits()
    assert ServerLimits().ceiling == CeilingLimits()
    assert ServerLimits(replay=None).replay is None
    assert ServerLimits(ceiling=None).ceiling is None


def test_limits_are_frozen_values() -> None:
    with pytest.raises(dataclasses.FrozenInstanceError):
        ServerLimits().ceiling = None  # type: ignore[misc]  # the point is that it is refused


async def test_a_connection_over_its_ceiling_is_refused_and_the_refusal_is_reported(
    fake_clock: FakeClock, refusals: list[ConnectionRefusal], on_refusal: RefusalHandler
) -> None:
    limits = ServerLimits(replay=None, ceiling=CEILING)
    limiter = ServerLimiter(limits, fake_clock, on_refusal).for_connection()

    assert [await limiter.frame_admitted() for _ in range(BURST + 1)] == [True, True, False]

    # Reported before the False was returned, with no node: nothing had been decoded yet.
    assert refusals == [
        ConnectionRefusal(
            node_id=None, kind=RefusalKind.FRAME_CEILING, envelope_id=None, at=fake_clock.now()
        )
    ]


async def test_a_ceiling_refusal_names_the_node_of_the_last_envelope_decoded(
    fake_clock: FakeClock,
    make_envelope: MakeEnvelope,
    refusals: list[ConnectionRefusal],
    on_refusal: RefusalHandler,
) -> None:
    limits = ServerLimits(replay=None, ceiling=CEILING)
    limiter = ServerLimiter(limits, fake_clock, on_refusal).for_connection()
    envelope = make_envelope()
    assert await limiter.envelope_admitted(envelope) is True

    for _ in range(BURST + 1):
        await limiter.frame_admitted()

    assert [refusal.node_id for refusal in refusals] == [envelope.node_id]


async def test_a_duplicate_envelope_is_refused_and_reported_with_its_node_and_id(
    fake_clock: FakeClock,
    make_envelope: MakeEnvelope,
    refusals: list[ConnectionRefusal],
    on_refusal: RefusalHandler,
) -> None:
    limiter = ServerLimiter(ServerLimits(ceiling=None), fake_clock, on_refusal).for_connection()
    envelope = make_envelope()

    assert await limiter.envelope_admitted(envelope) is True
    assert refusals == []  # a first sighting is no refusal

    assert await limiter.envelope_admitted(envelope) is False
    assert refusals == [
        ConnectionRefusal(
            node_id=envelope.node_id,
            kind=RefusalKind.REPLAY,
            envelope_id=envelope.id,
            at=fake_clock.now(),
        )
    ]


async def test_connections_share_the_replay_window_but_each_has_its_own_bucket(
    fake_clock: FakeClock, make_envelope: MakeEnvelope, on_refusal: RefusalHandler
) -> None:
    limiter_for = ServerLimiter(ServerLimits(ceiling=CEILING), fake_clock, on_refusal)
    first, second = limiter_for.for_connection(), limiter_for.for_connection()
    envelope = make_envelope()

    # The window is the listener's: a frame seen on the first connection is a replay on the
    # second, which is what catches a replay after a reconnect.
    assert await first.envelope_admitted(envelope) is True
    assert await second.envelope_admitted(envelope) is False
    # The bucket is the connection's: spending the first's does not touch the second's.
    assert [await first.frame_admitted() for _ in range(BURST + 1)] == [True, True, False]
    assert [await second.frame_admitted() for _ in range(BURST)] == [True, True]


async def test_the_same_id_from_another_node_is_not_a_duplicate(
    fake_clock: FakeClock, make_envelope: MakeEnvelope, on_refusal: RefusalHandler
) -> None:
    limiter = ServerLimiter(ServerLimits(ceiling=None), fake_clock, on_refusal).for_connection()
    envelope = make_envelope()
    other_node = envelope.model_copy(update={"node_id": new_node_id(fake_clock)})

    assert await limiter.envelope_admitted(envelope) is True
    assert await limiter.envelope_admitted(other_node) is True


async def test_a_refusal_is_enforced_even_when_nobody_is_listening(
    fake_clock: FakeClock, make_envelope: MakeEnvelope
) -> None:
    limiter = ServerLimiter(ServerLimits(ceiling=CEILING), fake_clock, None).for_connection()
    envelope = make_envelope()

    assert await limiter.envelope_admitted(envelope) is True
    assert await limiter.envelope_admitted(envelope) is False
    assert [await limiter.frame_admitted() for _ in range(BURST + 1)] == [True, True, False]


async def test_switched_off_checks_admit_everything(
    fake_clock: FakeClock,
    make_envelope: MakeEnvelope,
    refusals: list[ConnectionRefusal],
    on_refusal: RefusalHandler,
) -> None:
    limits = ServerLimits(replay=None, ceiling=None)
    limiter = ServerLimiter(limits, fake_clock, on_refusal).for_connection()
    envelope = make_envelope()

    assert all([await limiter.frame_admitted() for _ in range(10 * BURST)])
    assert await limiter.envelope_admitted(envelope) is True
    assert await limiter.envelope_admitted(envelope) is True
    assert refusals == []


async def test_a_recorder_that_hangs_times_out_instead_of_holding_the_receive_path(
    fake_clock: FakeClock, make_envelope: MakeEnvelope, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(limits_module, "REFUSAL_REPORT_TIMEOUT_S", TINY_TIMEOUT_S)
    never = asyncio.Event()

    async def stuck(refusal: ConnectionRefusal) -> None:
        """A recorder that never returns."""
        await never.wait()

    limiter = ServerLimiter(ServerLimits(ceiling=None), fake_clock, stuck).for_connection()
    envelope = make_envelope()
    await limiter.envelope_admitted(envelope)

    # The timeout reaches the receiver unchanged: a refusal nobody could record must be loud.
    with pytest.raises(TimeoutError):
        await limiter.envelope_admitted(envelope)
