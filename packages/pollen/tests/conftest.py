"""Test composition root for the pollen package.

This module is where pollen's test suite wires together its collaborators once: a clock whose
sleeps are cut short so loops that pace themselves do not make the suite wait, a scripted Queen
(a real loopback WebSocket listener that speaks Waggle, from ``support/queen_double.py``), and a
device already enrolled against that Queen (the state and key an enrolment leaves in the data
directory). Per codingrules section 8.2, the composition root for any entry point is exactly one
file; for tests, that file is this one. The helpers themselves live in ``support/``, a directory
of its own on the test path so none of their module names can collide with another package's.

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Loaded automatically by pytest before every test
    module under packages/pollen/tests/. Nothing depends on it; it depends on pollen, waggle and
    the support helpers.

Key invariants:
    - Every fixture that opens a socket closes it on teardown: the suite turns a ResourceWarning
      into a failure.
    - A fixture never reads or writes outside ``tmp_path``.

See Also:
    - .claude/codingrules.md section 8.2 for the composition-root rule this file follows.
    - support/queen_double.py and support/device_kit.py for the helpers behind the fixtures.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from device_kit import Device, Live, live_lease, make_device
from fast_clock import FastClock
from queen_double import QueenDouble

from waggle.clock import Clock


@pytest.fixture
def clock() -> Clock:
    """A real clock whose sleeps are capped, so backoffs and heartbeats do not slow the suite."""
    return FastClock()


@pytest.fixture
async def queen(clock: Clock) -> AsyncIterator[QueenDouble]:
    """A scripted Queen listening on loopback, stopped when the test ends."""
    scripted = QueenDouble(clock)
    await scripted.start()
    try:
        yield scripted
    finally:
        await scripted.stop()


@pytest.fixture
def device(queen: QueenDouble, tmp_path: Path, clock: Clock) -> Device:
    """A device enrolled against ``queen``, with its data directory under ``tmp_path``."""
    return make_device(queen, tmp_path / "data", clock)


@pytest.fixture
async def live(queen: QueenDouble, device: Device, clock: Clock) -> AsyncIterator[Live]:
    """A running packet with the Queen connected to it and one lease open."""
    async with live_lease(queen, device, clock) as value:
        yield value
