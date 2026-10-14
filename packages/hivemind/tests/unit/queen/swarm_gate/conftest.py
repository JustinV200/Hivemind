"""Share the gate rig every swarm gate test starts from.

Fits into the Hive:
    Test infrastructure (codingrules section 14.5), not shipped. Provides `rig`: a swarm gate over a
    Queen, a registry, a hub and a Warden factory, nothing started, cleaned up after the test.

Key invariants:
    - A rig is closed after its test: the gate, every Warden, every link and the Queen's readers.
    - A move waits for no bee in real time in these tests (`steps.HANDOFF_PATIENCE_S` is 0).

See Also:
    - builders.swarm.gate for what a rig holds.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from builders.swarm import GateRig, make_gate_rig

from hivemind.queen.swarm_gate.colonize import steps


@pytest.fixture(autouse=True)
def moves_wait_for_no_bee(monkeypatch: pytest.MonkeyPatch) -> None:
    """Give a move no real time to wait for a bee's Handoff, in every test under this directory."""
    # A move waits, in real time, for a bee inside a command to write its Handoff. The scripted
    # bees of these tests write theirs at once or never, so there is nothing to wait for, and a
    # wait that ran out would be thirty seconds of a test doing nothing. A test that is about the
    # wait sets it back.
    monkeypatch.setattr(steps, "HANDOFF_PATIENCE_S", 0.0)


@pytest.fixture
async def rig() -> AsyncIterator[GateRig]:
    """A swarm gate over fakes, nothing started."""
    built = make_gate_rig()
    yield built
    await built.close()
