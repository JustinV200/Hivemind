"""Build a Pheromone Trail that fails on demand, for tests of what a bee's death does to its Warden.

A runtime that dies of an error it did not expect is rare in production and awkward to provoke in a
test, because every error a Worker's runtime handles on purpose is handled. A store that fails
under it is the plain way to get one: the trail it writes `worker.started` to is full, and the
write raises. `FailingTrail` is an in-memory trail that raises `OSError` the first `times` it is
asked to record one named kind of event, and records everything else (and that kind afterwards)
as usual, so a test can kill one bee's runtime and let the next one live.

Fits into the Hive:
    Test infrastructure (codingrules section 14.5), not shipped. Used by the Worker runtime's and
    the Warden's tests of a runtime that ended on an error of its own.

Key invariants:
    - Only the named kind ever fails, and only `times` times; every other event is recorded
      exactly as `MemoryPheromoneTrail` does.
    - `failures` counts the raises, so a test can assert the fault really fired.

See Also:
    - hivemind.pheromone.trail.memory for the trail this subclasses.
    - hivemind.workers.errors.WorkerRuntimeError for what such a failure becomes.
"""

from __future__ import annotations

from hivemind.pheromone import PheromoneEvent
from hivemind.pheromone.trail.memory import MemoryPheromoneTrail
from waggle.clock import Clock

__all__ = ["FailingTrail"]


class FailingTrail(MemoryPheromoneTrail):
    """An in-memory trail that cannot record one kind of event, the first `times` it is asked."""

    def __init__(self, clock: Clock, failing_kind: str, times: int = 1) -> None:
        """Build a trail that raises `OSError` when asked to record `failing_kind`.

        Args:
            clock: The clock the trail stamps and orders by.
            failing_kind: The event kind whose recording fails, e.g. `"worker.started"`.
            times: How many recordings of that kind fail before it records normally again.
        """
        super().__init__(clock)
        self._failing_kind = failing_kind
        self._remaining = times
        self.failures = 0  # How many recordings this trail has refused so far.

    async def record(self, event: PheromoneEvent) -> None:
        """Record `event`, unless it is the kind this trail cannot and a failure is still owed."""
        if event.kind == self._failing_kind and self._remaining > 0:
            self._remaining -= 1
            self.failures += 1
            raise OSError("no space left on device")
        await super().record(event)
