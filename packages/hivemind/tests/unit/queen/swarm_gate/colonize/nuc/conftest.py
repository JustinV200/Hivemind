"""Share the gate rig the Nuc flow tests start from: one that allows a device a model server.

Fits into the Hive:
    Test infrastructure (codingrules section 14.5), not shipped. Provides `rig`, replacing the
    swarm gate's own for this directory: the same gate over fakes, its numbers allowing the server
    `box` with two models, `small` (4 GiB) and `large` (12 GiB).

Key invariants:
    - A rig is closed after its test: the gate, every Warden, every link and the Queen's readers.

See Also:
    - builders.swarm.nuc for the scripted device Warden and the numbers.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from builders.swarm import GateRig, make_gate_rig, nuc_terms


@pytest.fixture
async def rig() -> AsyncIterator[GateRig]:
    """A swarm gate over fakes that allows `box`, nothing started."""
    built = make_gate_rig(nuc_terms())
    yield built
    await built.close()
