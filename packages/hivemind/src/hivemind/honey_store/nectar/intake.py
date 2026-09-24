"""Take Nectar into the Honey Store: size cap, label, scope, the Night Veil rule, trail events.

Nectar is raw information a bee (a Worker or a Warden) or the Hive itself brings back; the Honey
Store is the Hive's knowledge base, where the House Bee (the maintenance Worker) later ripens it
into searchable Honey. `NectarIntake` is the one door every deposit comes through: `submit` takes
a whole deposit in-process, `receive_chunk` takes Waggle chunks and reassembles them through
`ChunkGroups`. Either way intake refuses a deposit over `[honey.store] max_nectar_bytes`, labels
it (`hivemind.honey_store.clearance.intake_label`: the declared label raised to the provenance
floor) and keeps both halves of that label beside it (ADR-0034: the declared label, or the
default when none was declared, and the floor, which decide whether a judge may later lower it),
scopes it (`hivemind.honey_store.scope.scope_for_nectar`), applies the Night Veil rule
(ADR-0031: a Night Veil Cell -- the tier whose execution records never outlive teardown -- may
export only `RIPENED_HONEY` at C0/C1; everything else it deposits is kept as an ephemeral side
channel purged at teardown), and hands the draft to `HoneyStore.add_nectar`, which dedupes and
records the trail events this module builds, in one transaction.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy), inside `hivemind.honey_store.nectar`.
    Called by the Queen's tick (a Waggle deposit, `expire_groups` from her housekeeping) and
    in-process by the Queen and the House Bee (`submit`). Calls into `hivemind.honey_store`
    (clearance, scope, identity, errors, models, store) and `hivemind.pheromone`; the store and
    the Pheromone Trail (the append-only audit log) are where everything it decides lands.

Key invariants:
    - A Night Veil source's ephemeral deposits and refusals are never recorded (ADR-0031,
      codingrules section 12): the builder handed to `add_nectar` returns nothing, no
      `honey.nectar_rejected` is written, and no rejection is logged either. Its one export,
      `RIPENED_HONEY` at C0/C1, is ordinary Nectar that outlives the Cell by design and is
      recorded like any other deposit.
    - A stored label is never below `intake_label`'s result, and a stored tier is always the
      Queen's own record (`NectarSubmission.tier`), never what a sender claimed.
    - Every draft carries its declared label and its floor, so the stored label is always exactly
      the higher of the two (ADR-0034).
    - An ephemeral (Night Veil) draft never carries a `source_key`, so it can never be merged
      onto an ordinary row and raise that row's label from inside the boundary.
    - Every rejection is re-raised after it is recorded, so the Queen's tick can answer the
      sender with `control.error` carrying the error's stable `code`.
    - Event payloads carry ids, counts and enum values only: never content, a title or a scope's
      text beyond its kind.

See Also:
    - docs/adr/0031-honey-store-sqlite-fts5-sqlite-vec.md for the intake, labelling, scoping and
      Night Veil rules implemented here.
    - hivemind.honey_store.nectar.reassembly for ChunkGroups, the chunk rules.
    - hivemind.honey_store.store.protocol for HoneyStore.add_nectar and NectarEvents.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime

from hivemind.cell import CombShieldLevel, HoneyClearance
from hivemind.common.logging import get_logger
from hivemind.honey_store.clearance import intake_floor, intake_label
from hivemind.honey_store.errors import (
    CellMismatchError,
    NectarRejectedError,
    NectarTooLargeError,
    NightVeilRefusedError,
)
from hivemind.honey_store.identity import HoneyIdentity, honey_event
from hivemind.honey_store.models import NectarDraft
from hivemind.honey_store.nectar.reassembly import ChunkGroups
from hivemind.honey_store.nectar.submission import (
    DepositSource,
    IntakeResult,
    NectarSubmission,
    submission_from_deposit,
)
from hivemind.honey_store.scope import NectarProvenance, scope_for_nectar
from hivemind.honey_store.store import HoneyStore, NectarAdded, NectarEvents
from hivemind.manifest import HoneyStoreSection
from hivemind.pheromone import HoneyEvent
from waggle.clock import Clock
from waggle.messages.honey import NectarDeposit, NectarKind

SHA256_PREFIX_CHARS = 12  # Enough of a digest to tell deposits apart in an audit, never the whole.

__all__ = ["SHA256_PREFIX_CHARS", "NectarIntake"]

log = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class _Labels:
    """The label intake stores for one deposit, and the two facts it is made of (ADR-0034)."""

    label: HoneyClearance  # `intake_label`: the declared label raised to the floor.
    declared: HoneyClearance  # The depositor's own label, or `[honey.clearance] default_label`.
    floor: HoneyClearance  # `intake_floor`: what the deposit's provenance never lets it go below.


class NectarIntake:
    """The one door into the Honey Store: label, scope, cap and store every Nectar deposit.

    Owns one piece of mutable state, the `ChunkGroups` buffering incomplete Waggle deposits, and
    shares nothing else; call it from one event loop (the Queen's), as every method is async and
    the chunk buffer is not thread-safe.
    """

    def __init__(
        self,
        store: HoneyStore,
        identity: HoneyIdentity,
        clock: Clock,
        store_section: HoneyStoreSection,
        default_label: HoneyClearance,
    ) -> None:
        """Wire intake to its store, its event identity, its clock and its manifest settings.

        Args:
            store: Where every accepted deposit and its events land.
            identity: The Hive, node and actor stamped on every event intake records.
            clock: The injected time source, for chunk idle times and event timestamps.
            store_section: `[honey.store]`; its `max_nectar_bytes` caps every deposit.
            default_label: `[honey.clearance] default_label` as a `HoneyClearance`, used when a
                depositor declares no label at all.
        """
        self._store = store
        self._identity = identity
        self._clock = clock
        self._store_section = store_section
        self._default_label = default_label
        self._groups = ChunkGroups()

    async def submit(self, submission: NectarSubmission) -> IntakeResult:
        """Label, scope and store one whole deposit, deduping it onto an existing row if possible.

        Args:
            submission: The whole deposit and its provenance.

        Returns:
            The stored row, whether it was new, and whether it was kept ephemeral.

        Raises:
            NectarTooLargeError: The content is over `[honey.store] max_nectar_bytes`; recorded
                as `honey.nectar_rejected` unless the source is a Night Veil Cell.
            NightVeilRefusedError: A Night Veil Cell's `RIPENED_HONEY` labelled above C1;
                never recorded.
            InvalidScopeError: A HUMAN origin's proposed scope is malformed.
        """
        # Hashed here, once; receive_chunk passes the digest it already verified instead.
        content_sha256 = hashlib.sha256(submission.content).hexdigest()
        return await self._store_submission(submission, content_sha256)

    async def receive_chunk(
        self, deposit: NectarDeposit, source: DepositSource
    ) -> IntakeResult | None:
        """Take one Waggle chunk; store the deposit once its final chunk arrives and verifies.

        Args:
            deposit: One chunk of a deposit, as the Queen's inbox received it.
            source: What the Queen knows about the Warden it came from.

        Returns:
            The stored deposit's result on its final chunk; None while more chunks are awaited.

        Raises:
            NectarRejectedError: Any chunk rule failed (`CellMismatchError` for another Cell's
                deposit, or one of `ChunkGroups.accept`'s reasons), or the whole deposit was
                refused (`NightVeilRefusedError`); recorded as `honey.nectar_rejected` unless
                the source is a Night Veil Cell, and always re-raised.
        """
        whole = await self._accept_chunk(deposit, source)
        # Still waiting for more chunks of this deposit: nothing to store yet.
        if whole is None:
            return None
        submission = submission_from_deposit(deposit, whole, source)
        return await self._store_submission(submission, deposit.sha256)

    def expire_groups(self, now: datetime) -> int:
        """Drop every incomplete chunked deposit idle past the spec's timeout.

        Args:
            now: The current time; the Queen's tick passes its own clock's reading.

        Returns:
            How many incomplete deposits were dropped.
        """
        return self._groups.expire(now)

    async def _accept_chunk(self, deposit: NectarDeposit, source: DepositSource) -> bytes | None:
        """Feed one chunk to `ChunkGroups`; record (never for Night Veil) and re-raise a refusal."""
        try:
            # A Warden relays only its own Cell's deposits: the Queen's record of that Cell wins.
            if deposit.cell_id != source.cell_id:
                raise CellMismatchError(deposit.cell_id, source.cell_id)
            return self._groups.accept(
                source.sender, deposit, self._clock.now(), self._store_section.max_nectar_bytes
            )
        except NectarRejectedError as error:
            # Nothing about a Night Veil source's deposit outlives its teardown, a refusal
            # included (ADR-0031, codingrules section 12): no trail event and no log line.
            if source.tier is not CombShieldLevel.NIGHT_VEIL:
                log.warning("honey_store.nectar_rejected", code=error.code, cell_id=source.cell_id)
                # Local SQLite on the store's own thread, bounded by the connection's busy timeout.
                await self._store.record(self._chunk_rejected(error, deposit, source))
            raise

    async def _store_submission(
        self, submission: NectarSubmission, content_sha256: str
    ) -> IntakeResult:
        """Cap, label, scope and store one whole deposit; `submit`'s body, digest already known."""
        is_night_veil = submission.tier is CombShieldLevel.NIGHT_VEIL
        size = len(submission.content)
        max_bytes = self._store_section.max_nectar_bytes
        # The cap holds for in-process deposits too, which never passed through ChunkGroups.
        if size > max_bytes:
            error = NectarTooLargeError(size, max_bytes)
            if not is_night_veil:
                event = honey_event(
                    self._identity,
                    self._clock,
                    "honey.nectar_rejected",
                    submission.cell_id,
                    code=error.code,
                    size_bytes=size,
                )
                # Local SQLite, bounded by the connection's busy timeout.
                await self._store.record(event)
            raise error
        labels = _labels_for(submission, self._default_label)
        ephemeral = _is_ephemeral(submission, labels.label)
        draft = _draft_for(submission, labels, ephemeral)
        # An ephemeral deposit leaves no trail (ADR-0031); the Night Veil export (RIPENED_HONEY at
        # C0/C1) is ordinary Nectar that outlives the Cell by design, so it is recorded like any.
        events = _no_events if ephemeral else _nectar_events(self._identity, self._clock)
        # Local SQLite, one transaction for the row and its events; bounded by the busy timeout.
        added = await self._store.add_nectar(draft, content_sha256, events)
        return IntakeResult(nectar=added.nectar, is_new=added.is_new, ephemeral=ephemeral)

    def _chunk_rejected(
        self, error: NectarRejectedError, deposit: NectarDeposit, source: DepositSource
    ) -> HoneyEvent:
        """Build the `honey.nectar_rejected` event for a refused chunk: ids and counts only."""
        return honey_event(
            self._identity,
            self._clock,
            "honey.nectar_rejected",
            source.cell_id,
            code=error.code,
            sender=source.sender,
            sha256_prefix=deposit.sha256[:SHA256_PREFIX_CHARS],
            total_bytes=deposit.total_bytes,
        )


def _labels_for(submission: NectarSubmission, default_label: HoneyClearance) -> _Labels:
    """Decide a deposit's stored label, keeping the declared label and the floor it came from."""
    declared = submission.declared if submission.declared is not None else default_label
    floor = intake_floor(submission.origin, submission.from_borrowed_cell)
    label = intake_label(
        submission.declared, submission.origin, submission.from_borrowed_cell, default_label
    )
    return _Labels(label=label, declared=declared, floor=floor)


def _is_ephemeral(submission: NectarSubmission, label: HoneyClearance) -> bool:
    """Apply the Night Veil rule (ADR-0031): True when the deposit must stay an ephemeral row.

    Raises:
        NightVeilRefusedError: A Night Veil Cell's `RIPENED_HONEY` labelled above C1, which
            would carry Royal data across the one boundary that exists to contain it.
    """
    # Every other tier's deposits are ordinary Nectar.
    if submission.tier is not CombShieldLevel.NIGHT_VEIL:
        return False
    # Anything but ripened Honey is the Cell's own execution record: kept, but purged at teardown.
    if submission.kind is not NectarKind.RIPENED_HONEY:
        return True
    if label.rank > HoneyClearance.C1.rank:
        raise NightVeilRefusedError(submission.cell_id, submission.kind.value)
    # The one intentional export: ordinary Nectar, labelled origin_tier NIGHT_VEIL.
    return False


def _draft_for(submission: NectarSubmission, labels: _Labels, ephemeral: bool) -> NectarDraft:
    """Build the store draft for an accepted submission: its labels, scope and tier decided."""
    provenance = NectarProvenance(
        kind=submission.kind,
        origin=submission.origin,
        task_id=submission.task_id,
        cell_id=submission.cell_id,
        bee=submission.bee,
    )
    return NectarDraft(
        kind=submission.kind,
        origin=submission.origin,
        media_type=submission.media_type,
        title=submission.title,
        content=submission.content,
        task_id=submission.task_id,
        cell_id=submission.cell_id,
        bee=submission.bee,
        observed_at=submission.observed_at,
        clearance=labels.label,
        declared_clearance=labels.declared,
        floor_clearance=labels.floor,
        origin_tier=submission.tier,
        scope=scope_for_nectar(provenance, submission.proposed_scope),
        # The store dedupes by source_key before it looks at which side of the Night Veil
        # boundary a row is on, so an ephemeral draft carries none: it may only ever merge onto
        # its own Cell's ephemeral rows (by digest), never change an ordinary row's label.
        source_key=None if ephemeral else submission.source_key,
        event_id=submission.event_id,
        ephemeral_cell_id=submission.cell_id if ephemeral else None,
    )


def _nectar_events(identity: HoneyIdentity, clock: Clock) -> NectarEvents:
    """Build `add_nectar`'s events builder for an ordinary deposit (the Night Veil export too).

    The builder runs inside the store's transaction, on the store's own thread, once the outcome
    is known: pure and fast, it only mints events from the outcome it is handed.
    """

    def build(added: NectarAdded) -> tuple[HoneyEvent, ...]:
        """Return received-or-deduplicated, plus label_raised when a dedupe merge raised it."""
        nectar = added.nectar
        kind = "honey.nectar_received" if added.is_new else "honey.nectar_deduplicated"
        stored = honey_event(
            identity,
            clock,
            kind,
            nectar.id,
            nectar_kind=nectar.kind.value,
            origin=nectar.origin.value,
            clearance=nectar.clearance.value,
            size_bytes=nectar.size_bytes,
            scope_kind=nectar.scope.partition(":")[0],
        )
        # A duplicate that outranked the stored label raised it (and its Honey rows) in the same
        # transaction; the raise is recorded beside the dedupe, `from`/`to` as clearance names.
        if added.raised_from is None:
            return (stored,)
        raised = honey_event(
            identity,
            clock,
            "honey.label_raised",
            nectar.id,
            **{"from": added.raised_from.value, "to": nectar.clearance.value},
        )
        return (stored, raised)

    return build


def _no_events(added: NectarAdded) -> tuple[HoneyEvent, ...]:
    """Record nothing: a Night Veil Cell's ephemeral deposit never reaches the trail (ADR-0031)."""
    return ()
