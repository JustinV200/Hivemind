"""Name what a Supersedure acts through: the candidate, the devices, the Queen's own levers.

A Supersedure (ADR-0065) touches three things outside its own trail. The candidate machine is
reached over its `CellSession` (`CandidateLink`: put the archive, restore it, start the Queen, ask
whether she is ready, acknowledge her, or tell her to stand down). The devices hear the signed
`QueenMoved` (`NoticeSender`). The old Queen's own mode and providers are frozen, stood down or
woken (`QueenControls`). A held candidate asks whether the old Queen still answers
(`OldQueenProbe`). Each is a protocol so the driver is the same in production, over a real session
and the swarm hub, and in the in-process proof, over two Queens on temp stores. `SupersedureWiring`
is what a composition root hands the Queen, and `SupersedureBook` is her state for it.

Fits into the Hive:
    Layer 6 (the kernel), inside `hivemind.queen.supersedure`. Implemented by the composition root
    (`hivemind.cli.compose`) and by tests; read by `.copy`, `.handover`, `.rollback`, `.run`,
    `.gate` and `.door`. Calls into `hivemind.common.backup`, `.preflight` and waggle only.

Key invariants:
    - A `CandidateLink` method that cannot reach the candidate returns False (the ask methods) or
      raises `SupersedureFailedError` (the copy methods); it never hangs past its own bound.
    - `SupersedureBook.task` is set while one Supersedure runs in this process; there is never a
      second.

See Also:
    - docs/adr/0065-supersedure-moving-the-hive-stand.md.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from hivemind.common.backup import BackupSource
from hivemind.queen.supersedure.preflight import ProbeReport
from hivemind.swarm import SwarmNode
from waggle.messages.control import QueenMoved

if TYPE_CHECKING:
    # Types only: the two endings import this module for its protocols.
    from hivemind.queen.supersedure.handover import Handover
    from hivemind.queen.supersedure.rollback import Rollback

DEFAULT_GRACE_S = 600.0  # Ten minutes: a restore and a Queen's start, with room to spare.
DEFAULT_POLL_S = 2.0  # How often the old Queen asks whether the candidate is ready.

__all__ = [
    "DEFAULT_GRACE_S",
    "DEFAULT_POLL_S",
    "CandidateLink",
    "Endpoint",
    "NoticeSender",
    "OldQueenProbe",
    "QueenControls",
    "SupersedureBook",
    "SupersedureWiring",
]


@dataclass(frozen=True, slots=True)
class Endpoint:
    """Where a Queen is and who she signs as: the content of a `QueenMoved`.

    Attributes:
        node_id: Her node.
        address: Her Waggle endpoint (wss://, or ws:// on loopback).
        public_key_hex: The key her envelopes are signed with (the Hive key moves with her).
        tls_pin: Her listener certificate's SHA-256; None for ws://.
    """

    node_id: str
    address: str
    public_key_hex: str
    tls_pin: str | None


class CandidateLink(Protocol):
    """The candidate machine, over its session."""

    async def send_backup(self, archive: Path, sha256: str) -> None:
        """Put the archive on the candidate; raise `SupersedureFailedError` on failure."""
        ...

    async def restore(self, sha256: str) -> None:
        """Run `hive restore --sha256` there; raise `SupersedureFailedError` on failure."""
        ...

    async def start_queen(self) -> None:
        """Start the candidate's Queen from the copy; raise `SupersedureFailedError` on failure."""
        ...

    async def is_ready(self, started_id: str) -> bool:
        """Whether her trail holds `queen.supersedure_ready` for `started_id` (False: unreached)."""
        ...

    async def acknowledge(self, started_id: str) -> bool:
        """Record `queen.superseded` on her trail, lifting her hold; False if unreached."""
        ...

    async def discard(self, started_id: str) -> bool:
        """Record `queen.supersedure_aborted` on her trail, so she never runs; best effort."""
        ...


class NoticeSender(Protocol):
    """Every device link the Queen holds."""

    async def broadcast(self, notice: QueenMoved) -> int:
        """Send `notice`, signed with the Hive key, to every connected device; return how many."""
        ...


class QueenControls(Protocol):
    """The old Queen's own levers."""

    async def freeze(self) -> None:
        """Cluster every provider, then move CLUSTERED -> SUPERSEDING."""
        ...

    async def stand_down(self) -> None:
        """Move SUPERSEDING -> SUPERSEDED."""
        ...

    async def wake(self) -> None:
        """Move SUPERSEDING -> CLUSTERED, then resume every provider the freeze clustered."""
        ...


class OldQueenProbe(Protocol):
    """What a held candidate knows of the old Queen."""

    async def reachable(self) -> bool:
        """Whether the old Queen's address still answers."""
        ...


@dataclass(frozen=True, slots=True)
class SupersedureWiring:
    """What a composition root gives the Queen so a human can move her Hive Stand.

    Attributes:
        own: This Queen's endpoint, which a rollback points the devices back at.
        node_for: The swarm registry's record of a node, by node id; None when unknown.
        link_for: The candidate's session link, by node id.
        probe_for: The candidate's own answers, by node id; None when it cannot be asked.
        sender: Every device link.
        source: What the backup copies (`hivemind.cli.backup.backup_source`).
        endpoint_for: The candidate's endpoint once it serves, by node id.
        runtime_version: This Hive Stand's runtime version.
        workdir: Where the archive is written before it moves.
        grace_s: How long the candidate has to be ready.
        poll_s: How often the old Queen asks.
    """

    own: Endpoint
    node_for: Callable[[str], Awaitable[SwarmNode | None]]
    link_for: Callable[[str], Awaitable[CandidateLink]]
    probe_for: Callable[[str], Awaitable[ProbeReport | None]]
    sender: NoticeSender
    source: Callable[[], Awaitable[BackupSource]]
    endpoint_for: Callable[[str], Awaitable[Endpoint]]
    runtime_version: str
    workdir: Path
    grace_s: float = DEFAULT_GRACE_S
    poll_s: float = DEFAULT_POLL_S


@dataclass(slots=True)
class SupersedureBook:
    """The Queen's Supersedure state in this process (codingrules 8.5: owned and mutated here)."""

    wiring: SupersedureWiring | None = None  # None: this Hive cannot move (the door refuses).
    task: asyncio.Task[Handover | Rollback] | None = None  # The one Supersedure under way, if any.
    outcomes: list[Handover | Rollback] = field(default_factory=list)  # Finished ones, newest last.
