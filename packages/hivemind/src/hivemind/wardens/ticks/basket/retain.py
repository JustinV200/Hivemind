"""Deposit what a Worker retains: the Warden's `RetainSink`, hash re-checked, with provenance.

Nothing enters the Basket by side effect (ADR-0048). A Worker proposes `retain`, the Capping gate
verifies the file against its digest, and only then does the Worker hand a `RetainDeposit` to
this sink -- in process, since a Worker and its Warden share a process. The Warden does not trust
the Worker's scratch: it reads the staged file again, checks its hash and size against what the
gate verified (a mismatch is a `basket.deposit_refused`, never a deposit), marks the blob tainted
when its Worker or task is under a quarantine or the Cell was isolated, and deposits it through the
one door with the Worker's own provenance. On the Hive Stand that is the Hive's door and the blob
is done. In a Cell the door is the Warden's own: the blob lands in the Cell's store, becomes
resident and is shipped up in a background job (the Worker is never made to wait for the Queen),
the offer's reason carrying the Worker's summary so the Queen's re-deposit writes the Honey
record. A refusal is recorded and logged; it never raises into the Worker's tool call.

Fits into the Hive:
    Layer 5 (per-Cell supervisors; spawn and supervise Workers), inside the wardens package's
    ticks.basket sub-package. Built per sub-bee by `hivemind.wardens.ticks.tools.seams` and handed
    to its `WorkerContext.retain_sink` at spawn. Calls into `hivemind.basket`, `door`, `send`,
    `jobs` and `events` of this package.

Key invariants:
    - The bytes deposited are the bytes whose hash and size the gate verified, re-read here.
    - A tainted source marks the blob tainted, so it is never shipped or loaded.
    - Every deposit writes exactly one Honey record (the door's rule) and its events.
    - A failed deposit is recorded and logged, never raised to the Worker.

See Also:
    - docs/adr/0048-basket-inert-blobs-residency-and-transfer.md ("Nothing enters by side effect").
    - hivemind.workers.tools.comb.retain for the tool that hands this sink its deposit.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import ValidationError

from hivemind.basket import BasketError, DepositOutcome, DepositRequest, MediaKind
from hivemind.basket.models import MAX_SUMMARY_CHARS
from hivemind.common.logging import get_logger
from hivemind.wardens.ticks.basket.door import deposit_door
from hivemind.wardens.ticks.basket.events import own_cell_id, record_basket
from hivemind.wardens.ticks.basket.jobs import start_job
from hivemind.wardens.ticks.basket.send import ship_up
from hivemind.workers.tools.comb import RetainDeposit
from waggle.ids import TaskId, WorkerId

if TYPE_CHECKING:
    from hivemind.wardens.warden import Warden

__all__ = ["WardenRetainSink", "is_tainted"]

log = get_logger(__name__)


class WardenRetainSink:
    """One Worker's `RetainSink`: re-verify its staged file, deposit it and (in a Cell) ship up."""

    def __init__(self, warden: Warden, worker_id: WorkerId, task_id: TaskId) -> None:
        """Bind the sink to the Warden that deposits and the Worker and task it deposits for.

        Args:
            warden: The depositing Warden.
            worker_id: The Worker whose artefacts these are (the blob's `origin_bee`).
            task_id: Its task (the blob's `origin_task_id`).
        """
        self._warden = warden
        self._worker_id = worker_id
        self._task_id = task_id

    async def retain(self, deposit: RetainDeposit) -> None:
        """Deposit the staged file `deposit` names, or record why not; never raises to the Worker.

        Args:
            deposit: The staged path, description and digest the Capping gate verified.
        """
        warden = self._warden
        content = await self._staged_bytes(deposit)
        door, cell = deposit_door(warden), warden._cell
        if content is None or door is None or cell is None:
            if content is not None:
                log.warning("wardens.retain.no_door", sha256=deposit.sha256)
            return
        request = self._request(deposit, content)
        if request is None:
            await self._refuse(deposit, "invalid_request")
            return
        try:
            # Local disk and SQLite, plus (in a Cell) a Nectar send: bounded, no model, no wait.
            outcome = await door.deposit(request)
        except BasketError as refused:
            for event in refused.events:
                await warden._deps.trail.record(event)
            log.warning("wardens.retain.refused", sha256=deposit.sha256, error=str(refused))
            return
        await self._settle(outcome, deposit)

    async def _staged_bytes(self, deposit: RetainDeposit) -> bytes | None:
        """Read the staged file again and return it only if it is what the gate verified."""
        try:
            session = self._warden._session
            if session is None:
                return None
            content = await session.get_file(Path(deposit.path))
        except (FileNotFoundError, OSError):
            await self._refuse(deposit, "staged_file_missing")
            return None
        # Scratch is writable by the Worker: the digest the gate checked is the only truth.
        if (
            len(content) != deposit.size_bytes
            or hashlib.sha256(content).hexdigest() != deposit.sha256
        ):
            await self._refuse(deposit, "hash_mismatch")
            return None
        return content

    def _request(self, deposit: RetainDeposit, content: bytes) -> DepositRequest | None:
        """Build the door's request with the Worker's provenance; None if a field is invalid."""
        cell = self._warden._cell
        if cell is None:
            return None
        try:
            return DepositRequest(
                content=content,
                title=deposit.title,
                # The Honey record is one paragraph (the model refuses a blank line): fold it.
                summary=" ".join(deposit.summary.split())[:MAX_SUMMARY_CHARS] or deposit.title,
                media_kind=_media_kind(deposit.media_kind),
                media_type=deposit.media_type,
                origin_cell_id=cell.id,
                origin_task_id=self._task_id,
                origin_bee=self._worker_id,
                from_borrowed_cell=cell.is_borrowed,
                tier=cell.comb_shield,
                tainted=is_tainted(self._warden, self._worker_id, self._task_id),
            )
        except ValidationError:
            return None

    async def _settle(self, outcome: DepositOutcome, deposit: RetainDeposit) -> None:
        """Record the door's events; in a Cell, make the blob resident and ship it up."""
        warden = self._warden
        for event in outcome.events:
            await warden._deps.trail.record(event)
        if warden._deps.royal_jelly is not None or warden._deps.resident_basket is None:
            return  # The Hive's own store: already canonical, nothing to ship.
        decision = await warden._deps.resident_basket.admit(outcome.blob)
        if decision.admit and not outcome.blob.tainted:
            # The Queen writes the blob's Honey record on re-deposit, from this reason.
            reason = " ".join(deposit.summary.split()) or deposit.title
            start_job(warden, _ship(warden, deposit.sha256, reason))

    async def _refuse(self, deposit: RetainDeposit, code: str) -> None:
        """Record `basket.deposit_refused` for a deposit that never reached the door."""
        log.warning("wardens.retain.deposit_refused", sha256=deposit.sha256, code=code)
        await record_basket(
            self._warden,
            "basket.deposit_refused",
            {
                "code": code,
                "sha256": deposit.sha256,
                "size_bytes": deposit.size_bytes,
                "media_kind": deposit.media_kind,
                "cell_id": str(own_cell_id(self._warden)),
            },
        )


def is_tainted(warden: Warden, worker_id: WorkerId, task_id: TaskId) -> bool:
    """Return whether anything this Warden knows marks the bee, its task or its Cell suspect.

    Args:
        warden: The Warden whose quarantine record and isolation flag are read.
        worker_id: The depositing bee.
        task_id: Its task.

    Returns:
        True when the task is held by a quarantine, the bee is the one quarantined, or the Queen
        isolated this Cell. (The memory ledgers have no read for "is this scope tainted", only for
        "what is left to taint", so the Warden's own record of what it was told is the source.)
    """
    held = warden._quarantined
    return task_id in held or any(r.bee == worker_id for r in held.values()) or warden._isolated


async def _ship(warden: Warden, sha256: str, reason: str) -> None:
    """Background job: ship one retained blob up; the outcome is on the trail either way."""
    await ship_up(warden, sha256, reason)


def _media_kind(tag: str) -> MediaKind:
    """Map a Worker's lowercase kind tag to the Basket's closed kinds; OTHER for an unknown one."""
    try:
        return MediaKind[tag.upper()]
    except KeyError:
        return MediaKind.OTHER
