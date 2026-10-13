"""Fixtures the Swarm unit tests share: a running link to a scripted device.

Most Swarm tests need a started `DeviceLink` attached to a hub with a `ScriptedDevice` answering
on the other end of an in-memory transport. The `rig` fixture builds one with the default script
and closes it when the test ends; a test that needs a different script builds its own with
`builders.swarm.make_link_rig` and closes it with `try`/`finally` or the `rigs` fixture, which
closes every rig it was handed.

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Loaded automatically by pytest for every test
    module under tests/unit/swarm. Depends on builders.swarm; nothing else depends on it.

Key invariants:
    - Every rig a fixture hands out is closed after the test, whatever the test did.

See Also:
    - builders.swarm.rig for the rig itself.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable

import pytest
from builders.swarm import DeviceScript, LinkRig, make_link_rig


@pytest.fixture
async def rig() -> AsyncIterator[LinkRig]:
    """A started link to a scripted device with the default script."""
    running = await make_link_rig()
    yield running
    await running.close()


@pytest.fixture
async def rigs() -> AsyncIterator[Callable[[DeviceScript], Awaitable[LinkRig]]]:
    """A factory of rigs with a given script, all closed when the test ends."""
    made: list[LinkRig] = []

    async def build(script: DeviceScript) -> LinkRig:
        """Build and remember a rig whose device follows `script`."""
        running = await make_link_rig(script)
        made.append(running)
        return running

    yield build
    for running in made:
        await running.close()
