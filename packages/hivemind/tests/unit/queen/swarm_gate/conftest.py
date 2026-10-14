"""Share the gate rig every swarm gate test starts from.

Fits into the Hive:
    Test infrastructure (codingrules section 14.5), not shipped. Provides `rig`: a swarm gate over a
    Queen, a registry, a hub and a Warden factory, nothing started, cleaned up after the test.

Key invariants:
    - A rig is closed after its test: the gate, every Warden, every link and the Queen's readers.

See Also:
    - builders.swarm.gate for what a rig holds.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from builders.swarm import GateRig, make_gate_rig


@pytest.fixture
async def rig() -> AsyncIterator[GateRig]:
    """A swarm gate over fakes, nothing started."""
    built = make_gate_rig()
    yield built
    await built.close()
