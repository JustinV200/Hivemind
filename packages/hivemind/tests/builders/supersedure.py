"""Build Supersedure test doubles: a candidate, the devices, the Queen's levers, an old Queen.

ADR-0065's driver acts only through `hivemind.queen.supersedure.ports`, so a unit test runs it
end to end over these fakes and an in-memory trail. Every fake appends what it was asked to one
shared `calls` list, so a test can assert the order ADR-0065 fixes (stand down, then
acknowledge, then announce).

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Used by tests/unit/queen/supersedure.

Key invariants:
    - None of these touches a disk, a socket or a clock of its own.

See Also:
    - hivemind.queen.supersedure.ports for the protocols implemented here.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path

from hivemind.pheromone import MemoryPheromoneTrail
from hivemind.queen.errors import SupersedureFailedError
from hivemind.queen.supersedure import Endpoint, Recorder
from waggle.clock import FakeClock
from waggle.ids import HiveId, NodeId, new_hive_id, new_node_id
from waggle.messages.control import QueenMoved

KEY_HEX = "ab" * 32  # The Hive key's public half; it moves with the Queen.

__all__ = [
    "KEY_HEX",
    "FakeCandidate",
    "FakeControls",
    "FakeOldQueen",
    "FakeSender",
    "endpoint",
    "make_recorder",
]


def make_recorder(
    clock: FakeClock, hive_id: HiveId | None = None, node_id: NodeId | None = None
) -> Recorder:
    """A Recorder over a fresh in-memory trail, as one node of one Hive."""
    return Recorder(
        trail=MemoryPheromoneTrail(clock),
        hive_id=hive_id or new_hive_id(clock),
        node_id=node_id or new_node_id(clock),
        clock=clock,
    )


def endpoint(node_id: str, port: int) -> Endpoint:
    """A loopback Queen endpoint (ws://, so no pin)."""
    return Endpoint(node_id, f"ws://127.0.0.1:{port}", KEY_HEX, None)


@dataclass
class FakeCandidate:
    """A candidate machine: ready after `ready_after` asks, or never; a step may fail or stall."""

    calls: list[str]
    ready_after: int | None = 0
    fail_step: str | None = None
    stalls: bool = False  # The restore never returns, as a candidate that stopped answering.
    received: list[str] = field(default_factory=list)  # The digests it was sent.

    async def send_backup(self, archive: Path, sha256: str) -> None:
        """Keep the digest; the archive must exist while this runs."""
        self._step("copy", await asyncio.to_thread(archive.is_file))
        self.received.append(sha256)

    async def restore(self, sha256: str) -> None:
        """Restore, unless this step is the one that fails; or never return, when it stalls."""
        self._step("restore", sha256 in self.received)
        if self.stalls:
            await asyncio.Event().wait()

    async def start_queen(self) -> None:
        """Start, unless this step is the one that fails."""
        self._step("start", True)

    async def is_ready(self, started_id: str) -> bool:
        """Ready once asked more than `ready_after` times."""
        self.calls.append("is_ready")
        asked = self.calls.count("is_ready")
        return self.ready_after is not None and asked > self.ready_after

    async def acknowledge(self, started_id: str) -> bool:
        """Note the acknowledgement."""
        self.calls.append("acknowledge")
        return True

    async def discard(self, started_id: str) -> bool:
        """Note the discard."""
        self.calls.append("discard")
        return True

    def _step(self, step: str, ok: bool) -> None:
        """Record `step`, failing it as asked."""
        self.calls.append(step)
        if step == self.fail_step or not ok:
            raise SupersedureFailedError(step, "the test failed it")


@dataclass
class FakeSender:
    """Every device link: keeps each notice."""

    calls: list[str]
    notices: list[QueenMoved] = field(default_factory=list)

    async def broadcast(self, notice: QueenMoved) -> int:
        """Keep `notice`; two devices heard it."""
        self.calls.append("broadcast")
        self.notices.append(notice)
        return 2


@dataclass
class FakeControls:
    """The old Queen's levers: notes each one pulled."""

    calls: list[str]

    async def freeze(self) -> None:
        """Note the freeze."""
        self.calls.append("freeze")

    async def stand_down(self) -> None:
        """Note the stand-down."""
        self.calls.append("stand_down")

    async def wake(self) -> None:
        """Note the wake."""
        self.calls.append("wake")


@dataclass
class FakeOldQueen:
    """What a held candidate sees of the old Queen."""

    answers: bool

    async def reachable(self) -> bool:
        """Whether she answers."""
        return self.answers
