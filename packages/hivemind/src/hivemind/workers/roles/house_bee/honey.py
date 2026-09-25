"""Deposit what the House Bee sends down to Honey: aged Bee Bread and retired Cell Wax.

A House Bee (the maintenance role) keeps memory flowing down its tiers: hot state into Bee Bread
(the warm tier, found by id, time or task, never searched), and, here, Bee Bread into Honey (the
cold tier, the Hive's searchable knowledge base) once an entry is older than `[honey.ripening]
bee_bread_after_s` (roadmap step 7.6). `deposit_aged_bee_bread` walks Bee Bread past a watermark
the Honey Store keeps and hands every entry with content of its own to Nectar intake (the Honey
Store's one door) as `BEE_BREAD` Nectar: a transcript, tool result, compacted summary or demoted
note as plain text, and a Handoff (a bee's resumable snapshot) as its JSON document under the same
`handoff:<event id>` key the Worker's own Waggle deposit of it carries, so the two become one row.
`deposit_retired_wax` does the same for Cell Wax (a Queen-written caution about one Cell) once it
is CLEARED or EXPIRED (roadmap step 7.9a), so a Cell's history of cautions compounds as Honey at
`cell:<id>` scope without living in hot state. Neither duty ripens anything itself: the Ripener
(`hivemind.workers.roles.house_bee.loop.HouseBeeRipening`'s pass) turns deposits into Honey rows.
The caller names the Cell each deposit was gathered on (`CellRecords`), because the label floor
and the Night Veil rule both follow from that Cell's own record, never from a guess.

Fits into the Hive:
    Layer 4 (roles that do the work), inside `hivemind.workers.roles.house_bee`. Called by
    `hivemind.workers.roles.house_bee.sweep.run_sweep` when `SweepDeps.honey` is set (the Queen's
    housekeeping sweep sets it). Calls into `hivemind.cell`, `hivemind.honey_store` (intake and
    the store's watermark), `hivemind.memory` (Bee Bread, Handoffs, Cell Wax) and waggle only.

Key invariants:
    - The `bee_bread` watermark advances only past entries a sweep settled (deposited, or passed
      over for a reason retrying can never change); a store failure stops the batch where it
      stands, so the next sweep resumes from the last entry that did land.
    - Nothing gathered on a Night Veil Cell is ever deposited: Bee Bread and Cell Wax are not the
      one export that boundary allows (codingrules section 12).
    - Every deposit declares its own source's label; intake only ever raises it, never lowers it.
    - A Cell the caller holds no live record of counts as borrowed, so its material gets the C2
      floor: an unknown Cell is never trusted to be less sensitive than a Real one.
    - Neither duty raises for a failed deposit: Bee Bread and Cell Wax are already durable where
      they are, so a failure is logged and retried on a later sweep.

See Also:
    - docs/adr/0035-honey-store-sqlite-fts5-sqlite-vec.md for origins, source keys and labels.
    - .claude/codingrules.md section 8.9 for "demotion is a duty, not an emergency".
    - hivemind.workers.roles.house_bee.sweep for run_sweep, the one caller of both duties.
    - hivemind.honey_store.nectar for NectarIntake, the door every deposit goes through.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Protocol

from hivemind.cell import Cell, CombShieldLevel, HoneyClearance
from hivemind.common.errors import HiveMindError
from hivemind.common.logging import get_logger
from hivemind.honey_store import (
    HoneyAccess,
    NectarOrigin,
    NectarRejectedError,
    NectarSubmission,
    handoff_source_key,
    raise_label,
)
from hivemind.memory import BeeBreadEntry, BeeBreadEntryKind, CellWax, HandoffNotFoundError
from hivemind.memory.cell_wax import WaxState
from waggle.errors import InvalidIdError
from waggle.ids import CellId, EventId, IdKind, TaskId, WardenId, WorkerId, parse_id
from waggle.messages.honey import NectarKind
from waggle.messages.honey.exchange import MAX_TITLE_CHARS

if TYPE_CHECKING:
    # Type hints only: sweep.py imports this module at runtime to call both duties, so a runtime
    # import back would cycle; `from __future__ import annotations` keeps these names strings.
    from hivemind.memory import MemoryStore
    from hivemind.workers.roles.house_bee.sweep import SweepDeps, SweepWindow

BEE_BREAD_WATERMARK = "bee_bread"  # The store's named cursor: the last Bee Bread entry settled.
BEE_BREAD_SOURCE_KEY_PREFIX = "bee_bread:"  # ADR-0035's dedupe key for a non-Handoff entry.
WAX_SOURCE_KEY_PREFIX = "wax:"  # ADR-0035's dedupe key for one retired Cell Wax note.
TEXT_MEDIA_TYPE = "text/plain"  # Transcripts, tool results, summaries, notes and wax history.
JSON_MEDIA_TYPE = "application/json"  # A Handoff document, re-indented by ripening's decoder.
_MARK_SEPARATOR = "|"  # Joins a watermark's timestamp and entry id; neither ever contains it.
_WORKER_PREFIX = f"{IdKind.WORKER.value}_"  # What a Worker id starts with (waggle.ids).
_WARDEN_PREFIX = f"{IdKind.WARDEN.value}_"  # What a Warden id starts with (waggle.ids).
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)  # The lower bound before any watermark is set.
_RETIRED_STATES = frozenset({WaxState.CLEARED, WaxState.EXPIRED})  # Wax that left WRITTEN for good.
# What each Bee Bread kind with content of its own becomes; TASK_HISTORY and TRAIL_EVENT entries
# only index records whose homes are the Brood Chamber and the trail, so they are passed over.
_NECTAR_KIND: dict[BeeBreadEntryKind, NectarKind] = {
    BeeBreadEntryKind.TRANSCRIPT: NectarKind.TRANSCRIPT,
    BeeBreadEntryKind.TOOL_RESULT: NectarKind.TOOL_RESULT,
    BeeBreadEntryKind.SUMMARY: NectarKind.FINDING,
    BeeBreadEntryKind.NOTE: NectarKind.FINDING,
    BeeBreadEntryKind.HANDOFF: NectarKind.HANDOFF,
}
_TITLE_NOUN: dict[BeeBreadEntryKind, str] = {
    BeeBreadEntryKind.TRANSCRIPT: "Transcript",
    BeeBreadEntryKind.TOOL_RESULT: "Tool result",
    BeeBreadEntryKind.SUMMARY: "Compacted summary",
    BeeBreadEntryKind.NOTE: "Note",
    BeeBreadEntryKind.HANDOFF: "Handoff",
}
# One deposit's failures that retrying can never change: intake refusing the content, a Handoff
# that no longer exists, or content no NectarSubmission can carry (pydantic's error is a
# ValueError). The entry is passed over and the watermark moves past it.
_REFUSALS = (NectarRejectedError, HandoffNotFoundError, ValueError)
# Failures that stop a batch where it stands: a store (either tier's) or the database under it.
_STORE_FAILURES = (HiveMindError, sqlite3.Error)

__all__ = [
    "BEE_BREAD_SOURCE_KEY_PREFIX",
    "BEE_BREAD_WATERMARK",
    "JSON_MEDIA_TYPE",
    "TEXT_MEDIA_TYPE",
    "WAX_SOURCE_KEY_PREFIX",
    "CellRecords",
    "GatheredOn",
    "HouseBeeHoney",
    "bee_or_none",
    "deposit_aged_bee_bread",
    "deposit_retired_wax",
]

log = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class GatheredOn:
    """The Cell some material was gathered on, as the depositing principal's own records say.

    Attributes:
        cell_id: The Cell the material came from.
        from_borrowed_cell: Whether that Cell is borrowed (Real): intake's C2 floor follows it.
        tier: That Cell's Comb Shield tier; a Night Veil Cell's material is never deposited.
    """

    cell_id: CellId
    from_borrowed_cell: bool
    tier: CombShieldLevel

    @classmethod
    def of(cls, cell: Cell) -> GatheredOn:
        """Describe a Cell the caller holds a live record of.

        Args:
            cell: The Cell record (a Queen's own `WardenLink.cell`).

        Returns:
            Its id, whether it is borrowed, and its tier, read from the record itself.
        """
        return cls(cell_id=cell.id, from_borrowed_cell=cell.is_borrowed, tier=cell.comb_shield)

    @classmethod
    def unrecorded(cls, cell_id: CellId) -> GatheredOn:
        """Describe a Cell the caller no longer holds a record of (detached or destroyed).

        Args:
            cell_id: The Cell's id, as the material itself names it.

        Returns:
            The Cell, treated as borrowed at MEADOW: an unknown Cell earns the C2 floor rather
            than the benefit of the doubt (module docstring's "Key invariants").
        """
        return cls(cell_id=cell_id, from_borrowed_cell=True, tier=CombShieldLevel.MEADOW)


class CellRecords(Protocol):
    """Where a House Bee learns which Cell its deposits were gathered on; the caller's records.

    The Queen implements it over her Brood Chamber and her attached Warden links
    (`hivemind.queen.ticks.housekeeping`), since only she knows where each task ran.
    """

    async def gathered_on(self, task_id: TaskId | None) -> GatheredOn | None:
        """Return the Cell a task's material (or task-less material) was gathered on.

        Args:
            task_id: The task the material concerns; None for material that concerns no task,
                which the caller attributes to its own home Cell.

        Returns:
            The Cell, or None when the caller cannot name one (the deposit is then skipped).
        """
        ...

    def for_cell(self, cell_id: CellId) -> GatheredOn:
        """Describe one Cell by id: its live record, else `GatheredOn.unrecorded`.

        Args:
            cell_id: The Cell a piece of material names directly (a wax note's own Cell).

        Returns:
            What the caller's records say about that Cell.
        """
        ...


@dataclass(frozen=True, slots=True)
class HouseBeeHoney:
    """What a sweep needs to deposit into Honey: the Honey Store's handles and the Cell records.

    Attributes:
        access: The Hive's Honey Store: intake, the store's watermark and `[honey.ripening]`.
        cells: Where each deposit's Cell comes from (`CellRecords`).
    """

    access: HoneyAccess
    cells: CellRecords


@dataclass(frozen=True, slots=True)
class _Content:
    """One Bee Bread entry's deposit, before its Cell is known: what it is and what it says."""

    kind: NectarKind  # What the entry becomes.
    media_type: str  # How ripening decodes it.
    body: bytes  # The whole content, as deposited.
    declared: HoneyClearance  # The source's own label.
    source_key: str  # ADR-0035's dedupe key.
    task_id: TaskId | None  # The task it concerns, if any.
    event_id: EventId | None = None  # A Handoff's memory.checkpoint event id.
    bee: WorkerId | WardenId | None = None  # The bee that wrote it, when the entry names one.


async def deposit_aged_bee_bread(deps: SweepDeps, window: SweepWindow) -> int:
    """Deposit Bee Bread older than `bee_bread_after_s`, past the watermark, as BEE_BREAD Nectar.

    Args:
        deps: The sweep's collaborators; `honey` (the Honey Store and Cell records), `bee_bread`
            and `memory.store` (for Handoffs) are read. Nothing happens when `honey` is None.
        window: The sweep's `now` (the age cutoff counts back from it) and its `allowance`
            (entries labelled above it are never read).

    Returns:
        How many new Nectar rows this sweep added; a duplicate (a Handoff its Worker already
        deposited over Waggle) merges into the existing row and is not counted.
    """
    honey = deps.honey
    if honey is None:
        return 0  # No Honey Store wired: Bee Bread stays warm, exactly as before phase 7.
    deposited = 0
    settled: BeeBreadEntry | None = None
    try:
        # One batch per sweep, oldest first, so a slow week drains over a few sweeps.
        for entry in await _aged_batch(deps, honey, window):
            deposited += await _deposit_entry(entry, deps, honey)
            settled = entry
    except _STORE_FAILURES as error:
        # Stop where we stand: every entry after the last settled one is retried next sweep.
        log.warning("house_bee.bee_bread_deposit_stopped", reason=_reason(error))
    if settled is not None:
        await _advance_watermark(honey, settled)
    return deposited


async def deposit_retired_wax(deps: SweepDeps, window: SweepWindow) -> int:
    """Deposit every CLEARED or EXPIRED Cell Wax note not yet in the Honey Store (roadmap 7.9a).

    Args:
        deps: The sweep's collaborators; `honey` and `memory.store` are read. Nothing happens
            when `honey` is None.
        window: The sweep's `allowance`: notes labelled above it are never read.

    Returns:
        How many notes became new Nectar rows this sweep, at most `max_bee_bread_per_sweep`: the
        same per-sweep budget as Bee Bread, so one sweep stays short however much wax retired.
    """
    honey = deps.honey
    if honey is None:
        return 0  # No Honey Store wired: retired wax stays in the memory tables alone.
    budget = honey.access.ripening.max_bee_bread_per_sweep
    deposited = 0
    try:
        retired = await deps.memory.store.list_wax(None, _RETIRED_STATES, window.allowance)
        # Every retired note is checked by its own key, so a note deposited before is never
        # re-read; the budget bounds only what this sweep adds.
        for wax in retired:
            if deposited >= budget:
                break  # The rest wait for the next sweep.
            deposited += await _deposit_wax(wax, honey)
    except _STORE_FAILURES as error:
        # Every note not yet deposited is found again next sweep by the same key check.
        log.warning("house_bee.wax_deposit_stopped", reason=_reason(error))
    return deposited


async def _aged_batch(
    deps: SweepDeps, honey: HouseBeeHoney, window: SweepWindow
) -> tuple[BeeBreadEntry, ...]:
    """Return the next `max_bee_bread_per_sweep` entries past the watermark and old enough."""
    ripening = honey.access.ripening
    cutoff = window.now - timedelta(seconds=ripening.bee_bread_after_s)
    mark = _decode_mark(await honey.access.store.get_watermark(BEE_BREAD_WATERMARK))
    start = mark[0] if mark is not None else _EPOCH
    # Inclusive on both ends: entries sharing the watermark's own timestamp come back too, and
    # the (created_at, id) comparison below drops the ones already settled.
    candidates = await deps.bee_bread.between(start, cutoff, window.allowance)
    fresh = [entry for entry in candidates if mark is None or (entry.created_at, entry.id) > mark]
    return tuple(fresh[: ripening.max_bee_bread_per_sweep])


async def _deposit_entry(entry: BeeBreadEntry, deps: SweepDeps, honey: HouseBeeHoney) -> int:
    """Hand one entry to intake; 1 for a new row, 0 when it was passed over or merged."""
    try:
        content = await _content_for(entry, deps.memory.store)
        if content is None:
            return 0  # An index-only or empty entry: nothing of its own to deposit.
        gathered = await honey.cells.gathered_on(content.task_id)
        if gathered is None or gathered.tier is CombShieldLevel.NIGHT_VEIL:
            # No Cell to attribute it to, or a Night Veil Cell's own record (module docstring).
            return 0
        result = await honey.access.intake.submit(_entry_submission(entry, content, gathered))
    except _REFUSALS as error:
        # Retrying can never change this outcome: pass the entry over, and say so.
        log.warning("house_bee.bee_bread_refused", entry_id=entry.id, reason=_reason(error))
        return 0
    return 1 if result.is_new else 0


async def _content_for(entry: BeeBreadEntry, store: MemoryStore) -> _Content | None:
    """Build what one entry deposits, or None when its kind or its content carries nothing."""
    kind = _NECTAR_KIND.get(entry.kind)
    if kind is None:
        return None  # TASK_HISTORY or TRAIL_EVENT: an index into another store, not content.
    if entry.kind is BeeBreadEntryKind.HANDOFF:
        return await _handoff_content(entry, store)
    # A payload kind carries its whole text; a NOTE only its (already bounded) preview.
    text = entry.text if entry.kind is BeeBreadEntryKind.NOTE else entry.payload
    if not text:
        return None  # Nothing to deposit; intake would refuse empty content anyway.
    return _Content(
        kind=kind,
        media_type=TEXT_MEDIA_TYPE,
        body=text.encode("utf-8"),
        declared=entry.clearance,
        source_key=f"{BEE_BREAD_SOURCE_KEY_PREFIX}{entry.id}",
        task_id=entry.task_id,
    )


async def _handoff_content(entry: BeeBreadEntry, store: MemoryStore) -> _Content | None:
    """Resolve a HANDOFF entry to its stored document, deposited under its shared Handoff key."""
    if not entry.ref_ids:
        return None  # A HANDOFF entry always names its checkpoint; one that does not has none.
    event_id = EventId(entry.ref_ids[0])
    # Local SQLite on the memory store's own thread, milliseconds; a missing Handoff raises
    # HandoffNotFoundError, which the caller treats as a refusal.
    handoff, stored_label = await store.get_handoff(event_id)
    return _Content(
        kind=NectarKind.HANDOFF,
        media_type=JSON_MEDIA_TYPE,
        body=handoff.model_dump_json().encode("utf-8"),
        # The entry's own label, raised to the stored Handoff's should the two ever differ.
        declared=raise_label(entry.clearance, stored_label),
        source_key=handoff_source_key(event_id),
        task_id=entry.task_id if entry.task_id is not None else handoff.task_id,
        event_id=event_id,
        bee=bee_or_none(handoff.written_by),
    )


def _entry_submission(
    entry: BeeBreadEntry, content: _Content, gathered: GatheredOn
) -> NectarSubmission:
    """Build the BEE_BREAD submission for one entry: observed when it entered Bee Bread."""
    noun = _TITLE_NOUN[entry.kind]
    title = (
        f"{noun} from Bee Bread"
        if content.task_id is None
        else f"{noun} for task {content.task_id}"
    )
    return NectarSubmission(
        kind=content.kind,
        origin=NectarOrigin.BEE_BREAD,
        media_type=content.media_type,
        title=title[:MAX_TITLE_CHARS],
        content=content.body,
        task_id=content.task_id,
        cell_id=gathered.cell_id,
        bee=content.bee,
        observed_at=entry.created_at,
        declared=content.declared,
        from_borrowed_cell=gathered.from_borrowed_cell,
        tier=gathered.tier,
        source_key=content.source_key,
        event_id=content.event_id,
    )


async def _deposit_wax(wax: CellWax, honey: HouseBeeHoney) -> int:
    """Hand one retired note to intake unless it is already there; 1 for a new row, else 0."""
    source_key = f"{WAX_SOURCE_KEY_PREFIX}{wax.id}"
    # Local SQLite: one indexed lookup, so a note deposited on an earlier sweep costs no more.
    if await honey.access.store.has_source(source_key):
        return 0
    gathered = honey.cells.for_cell(wax.cell_id)
    if gathered.tier is CombShieldLevel.NIGHT_VEIL:
        return 0  # Nothing about a Night Veil Cell is deposited (module docstring).
    try:
        result = await honey.access.intake.submit(_wax_submission(wax, gathered, source_key))
    except _REFUSALS as error:
        # The same note would be refused again; it stays in the memory tables regardless.
        log.warning("house_bee.wax_refused", wax_id=wax.id, reason=_reason(error))
        return 0
    return 1 if result.is_new else 0


def _wax_submission(wax: CellWax, gathered: GatheredOn, source_key: str) -> NectarSubmission:
    """Build the CELL_WAX submission for one retired note: observed when it was proposed."""
    return NectarSubmission(
        kind=NectarKind.FINDING,
        origin=NectarOrigin.CELL_WAX,
        media_type=TEXT_MEDIA_TYPE,
        title=f"Cell Wax {wax.severity.value} ({wax.state.value})",
        content=_wax_text(wax).encode("utf-8"),
        task_id=wax.task_id,
        cell_id=wax.cell_id,
        bee=bee_or_none(wax.proposer),
        observed_at=wax.proposed_at,
        declared=wax.clearance,
        from_borrowed_cell=gathered.from_borrowed_cell,
        tier=gathered.tier,
        source_key=source_key,
    )


def _wax_text(wax: CellWax) -> str:
    """Render a retired note as its Honey content: the caution, why, who and when."""
    proposer = wax.proposer if wax.proposer is not None else "the operator"
    lines = [
        f"Cell Wax {wax.severity.value} about Cell {wax.cell_id}, now {wax.state.value}.",
        f"Caution: {wax.text}",
        f"Reason: {wax.reason or 'none recorded'}",
        f"Proposed by: {proposer} ({wax.origin.value} origin)",
        f"Proposed at: {wax.proposed_at.isoformat()}",
    ]
    # Each later date appears only once the note's own life reached it.
    if wax.decided_at is not None:
        lines.append(f"Written at: {wax.decided_at.isoformat()}")
    if wax.cleared_at is not None:
        cause = wax.clear_cause.value if wax.clear_cause is not None else wax.state.value
        lines.append(f"Retired at: {wax.cleared_at.isoformat()} ({cause})")
    if wax.expires_at is not None:
        lines.append(f"Expiry: {wax.expires_at.isoformat()}")
    return "\n".join(lines)


def bee_or_none(value: str | None) -> WorkerId | WardenId | None:
    """Return `value` as a Worker or Warden id when it is one, else None.

    A Handoff's `written_by` and a wax note's `proposer` may name a role or the human rather than
    a bee, and provenance names a bee only when it can prove which one.

    Args:
        value: The recorded author, if any.

    Returns:
        The id unchanged when it is a well-formed `worker_` or `warden_` id; None otherwise.
    """
    if value is None:
        return None  # Nobody recorded: nothing to name.
    try:
        # The prefix says which kind of bee it would be; parse_id then proves the id well formed.
        if value.startswith(_WORKER_PREFIX):
            return WorkerId(parse_id(value, IdKind.WORKER))
        if value.startswith(_WARDEN_PREFIX):
            return WardenId(parse_id(value, IdKind.WARDEN))
    except InvalidIdError:
        # A bee's prefix on a malformed id: provenance cannot prove which bee it was.
        log.debug("house_bee.author_not_a_bee_id", prefix=value.partition("_")[0])
        return None
    return None  # A role name or the operator: not a bee at all.


async def _advance_watermark(honey: HouseBeeHoney, settled: BeeBreadEntry) -> None:
    """Move the watermark to `settled`; a failure is logged, since the next sweep only repeats."""
    try:
        # Local SQLite, one upsert. Re-depositing after a failed write is harmless: every entry
        # carries its own source key, so intake merges it onto the row it already made.
        await honey.access.store.set_watermark(BEE_BREAD_WATERMARK, _encode_mark(settled))
    except _STORE_FAILURES as error:
        log.warning("house_bee.bee_bread_watermark_unsaved", reason=_reason(error))


def _encode_mark(entry: BeeBreadEntry) -> str:
    """Encode an entry's position in Bee Bread's own `(created_at, id)` order."""
    return f"{entry.created_at.isoformat()}{_MARK_SEPARATOR}{entry.id}"


def _decode_mark(value: str | None) -> tuple[datetime, str] | None:
    """Decode a stored watermark; None when unset or unreadable (the sweep starts over)."""
    if value is None:
        return None  # Nothing settled yet: the first sweep starts from the beginning.
    at, separator, entry_id = value.partition(_MARK_SEPARATOR)
    try:
        return datetime.fromisoformat(at), entry_id
    except ValueError:
        # Only this module writes it, so this is corruption: starting over is safe (every
        # deposit dedupes by its own key), and saying so is the only other thing to do.
        log.warning("house_bee.bee_bread_watermark_unreadable", separator_found=bool(separator))
        return None


def _reason(error: Exception) -> str:
    """Name a failure by its stable code when it has one, else by its class, for a log line."""
    return error.code if isinstance(error, HiveMindError) else type(error).__name__
