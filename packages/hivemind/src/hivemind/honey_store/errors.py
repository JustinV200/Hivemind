"""Define the Honey Store's own error tree: bad lookups, intake refusals, label and scope misuse.

The Honey Store is the Hive's cold tier: raw Nectar (unprocessed findings a bee brings back) is
taken in, ripened into Honey (distilled, indexed knowledge), and retrieved by Workers before they
act (README "Core concept 6"). Every subsystem roots its errors in
`hivemind.common.errors.HiveMindError` (codingrules section 10); this module is that root for
`hivemind.honey_store`. `NectarRejectedError` is a family of ten concrete subclasses rather than
one class with a per-instance code, because `HiveMindError.code` is a `ClassVar[str]` and mypy
--strict refuses to narrow a `ClassVar` into an instance attribute in a subclass (every other
`errors.py` in the repository fixes `code` per class for the same reason) -- `except
NectarRejectedError:` still catches every reason at once, and each concrete subclass's `code` is
still exactly the dotted string ADR-0031's intake rules name.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy). Raised by `hivemind.honey_store.store`
    (not-found and lowering/scope refusals) and by `hivemind.honey_store.nectar` (intake refusals,
    a later dispatch). Calls into `hivemind.common.errors` and `waggle.ids` only.

Key invariants:
    - Every HoneyStoreError subclass sets its own `code`; no two unrelated classes share one.
    - Every `NectarRejectedError` subclass's `code` is one of the ten stable dotted strings named
      on the classes below, so a caller matches on `code`, never on the message text.

See Also:
    - .claude/codingrules.md section 10 for the exceptions and errors rules this module follows.
    - docs/adr/0031-honey-store-sqlite-fts5-sqlite-vec.md for the intake and labelling rules whose
      refusals these classes carry.
    - hivemind.honey_store.clearance for `check_lowering`, `LabelLoweringError`'s one raiser.
    - hivemind.honey_store.scope for `InvalidScopeError`'s raisers.
"""

from __future__ import annotations

from typing import ClassVar

from hivemind.common.errors import HiveMindError, NotFoundError
from waggle.ids import HoneyId, NectarId

__all__ = [
    "CellMismatchError",
    "ChunkMismatchError",
    "DepositLengthMismatchError",
    "DepositTimedOutError",
    "FirstChunkNotAtZeroError",
    "HoneyNotFoundError",
    "HoneyStoreError",
    "InvalidScopeError",
    "LabelLoweringError",
    "NectarNotFoundError",
    "NectarNotRipenableError",
    "NectarRejectedError",
    "NectarTooLargeError",
    "NightVeilRefusedError",
    "OffsetMismatchError",
    "Sha256MismatchError",
    "TooManyOpenDepositsError",
]


class HoneyStoreError(HiveMindError):
    """Root of every error `hivemind.honey_store` raises on purpose.

    Subclass this for a specific failure, as the classes below do; code that has nothing more
    specific to say may raise this directly.
    """

    code: ClassVar[str] = "hivemind.honey_store.error"


class NectarNotFoundError(NotFoundError):
    """Raise when a lookup by id finds no matching Nectar row in the Honey Store."""

    code: ClassVar[str] = "hivemind.honey_store.nectar_not_found"

    def __init__(self, nectar_id: NectarId) -> None:
        """Build the error for a missing Nectar row.

        Args:
            nectar_id: The id that was looked up and not found.
        """
        super().__init__(f"No Nectar with id {nectar_id!r} exists in the Honey Store.")
        self.nectar_id = nectar_id


class HoneyNotFoundError(NotFoundError):
    """Raise when a lookup by id finds no matching Honey row in the Honey Store."""

    code: ClassVar[str] = "hivemind.honey_store.honey_not_found"

    def __init__(self, honey_id: HoneyId) -> None:
        """Build the error for a missing Honey row.

        Args:
            honey_id: The id that was looked up and not found.
        """
        super().__init__(f"No Honey with id {honey_id!r} exists in the Honey Store.")
        self.honey_id = honey_id


class NectarNotRipenableError(HoneyStoreError):
    """Raise when a Nectar row must never ripen: a Night Veil Cell's own ephemeral side channel.

    Ripening one would copy it into Honey that outlives the Cell's teardown, and flip its state so
    the teardown purge (which deletes only EPHEMERAL rows) would miss the row itself (ADR-0031).
    """

    code: ClassVar[str] = "hivemind.honey_store.nectar_not_ripenable"

    def __init__(self, nectar_id: NectarId, state: str) -> None:
        """Build the error for a ripening attempt on a row whose state forbids it.

        Args:
            nectar_id: The row that was asked to ripen.
            state: Its current `NectarState` value.
        """
        super().__init__(f"Nectar {nectar_id!r} is {state} and can never ripen.")
        self.nectar_id = nectar_id
        self.state = state


class NectarRejectedError(HoneyStoreError):
    """Root of intake's ten refusal reasons; catch this to handle any of them alike.

    Every concrete subclass below fixes `code` to one stable, dotted reason string
    (docs/waggle/spec.md section 5's chunking rules and ADR-0031's Night Veil boundary); a caller
    that only needs to know a deposit was refused, not why, catches `NectarRejectedError` itself.
    """

    code: ClassVar[str] = "hivemind.honey_store.nectar_rejected"


class NectarTooLargeError(NectarRejectedError):
    """Raise when a Nectar deposit's declared total size is over the configured cap."""

    code: ClassVar[str] = "hivemind.honey_store.nectar_too_large"

    def __init__(self, total_bytes: int, max_bytes: int) -> None:
        """Build the error for an oversized deposit.

        Args:
            total_bytes: The deposit's declared `total_bytes`.
            max_bytes: The cap it exceeded (`[honey.store] max_nectar_bytes`).
        """
        super().__init__(
            f"Nectar deposit is {total_bytes} bytes, over the {max_bytes}-byte cap "
            "([honey.store] max_nectar_bytes)."
        )
        self.total_bytes = total_bytes
        self.max_bytes = max_bytes


class OffsetMismatchError(NectarRejectedError):
    """Raise when a Nectar chunk's offset does not continue the deposit it claims to join."""

    code: ClassVar[str] = "hivemind.honey_store.offset_mismatch"

    def __init__(self, expected_offset: int, chunk_offset: int) -> None:
        """Build the error for a chunk arriving out of order.

        Args:
            expected_offset: The byte offset the next chunk of this deposit must carry.
            chunk_offset: The offset the chunk actually carried.
        """
        super().__init__(
            f"Nectar chunk offset {chunk_offset} does not match the expected offset "
            f"{expected_offset}; chunks must arrive contiguous and in order."
        )
        self.expected_offset = expected_offset
        self.chunk_offset = chunk_offset


class TooManyOpenDepositsError(NectarRejectedError):
    """Raise when a sender already has as many incomplete deposits open as intake allows."""

    code: ClassVar[str] = "hivemind.honey_store.too_many_open_deposits"

    def __init__(self, sender: str, open_count: int, limit: int) -> None:
        """Build the error for a sender over its open-deposit limit.

        Args:
            sender: The envelope sender id whose open deposits are over the limit.
            open_count: How many incomplete deposits that sender already has open.
            limit: The cap it hit (`waggle.messages.base.MAX_OPEN_CHUNK_GROUPS`).
        """
        super().__init__(
            f"Sender {sender} has {open_count} open Nectar deposits, at the {limit}-deposit "
            "limit (waggle.messages.base.MAX_OPEN_CHUNK_GROUPS)."
        )
        self.sender = sender
        self.open_count = open_count
        self.limit = limit


class Sha256MismatchError(NectarRejectedError):
    """Raise when a completed deposit's reassembled content does not hash to its declared sha256."""

    code: ClassVar[str] = "hivemind.honey_store.sha256_mismatch"

    def __init__(self, declared_sha256: str, computed_sha256: str) -> None:
        """Build the error for a digest mismatch.

        Args:
            declared_sha256: The digest the deposit's chunks declared.
            computed_sha256: The digest the reassembled content actually hashes to.
        """
        super().__init__(
            f"Nectar deposit declared sha256 {declared_sha256} but its reassembled content "
            f"hashes to {computed_sha256}."
        )
        self.declared_sha256 = declared_sha256
        self.computed_sha256 = computed_sha256


class FirstChunkNotAtZeroError(NectarRejectedError):
    """Raise when the first chunk of a new deposit does not start at byte offset 0."""

    code: ClassVar[str] = "hivemind.honey_store.first_chunk_not_at_zero"

    def __init__(self, offset: int) -> None:
        """Build the error for a first chunk with a nonzero offset.

        Args:
            offset: The offset the opening chunk actually carried.
        """
        super().__init__(f"First Nectar chunk of a new deposit has offset {offset}, not 0.")
        self.offset = offset


class NightVeilRefusedError(NectarRejectedError):
    """Raise when a Night Veil Cell's deposit is not the one kind that may cross that boundary."""

    code: ClassVar[str] = "hivemind.honey_store.night_veil_refused"

    def __init__(self, cell_id: str, kind: str) -> None:
        """Build the error for a refused Night Veil deposit.

        Args:
            cell_id: The Night Veil Cell the deposit claims to come from.
            kind: The deposit's `NectarKind`, as a plain string (its `.value`).
        """
        super().__init__(
            f"Nectar of kind {kind} from Night Veil Cell {cell_id} may not be accepted; only "
            "RIPENED_HONEY at C0/C1 crosses that boundary (ADR-0031)."
        )
        self.cell_id = cell_id
        self.kind = kind


class DepositTimedOutError(NectarRejectedError):
    """Raise when an open chunk group sat idle past the deposit timeout and was discarded."""

    code: ClassVar[str] = "hivemind.honey_store.deposit_timed_out"

    def __init__(self, sender: str, sha256: str, idle_s: float) -> None:
        """Build the error for a discarded, idle-too-long deposit.

        Args:
            sender: The envelope sender id whose deposit timed out.
            sha256: The incomplete deposit's declared digest.
            idle_s: How long it sat with no new chunk.
        """
        super().__init__(
            f"Nectar deposit {sha256} from {sender} was idle {idle_s:.0f}s with no new chunk; "
            "the open chunk group was discarded (waggle.messages.base.CHUNK_GROUP_TIMEOUT_S)."
        )
        self.sender = sender
        self.sha256 = sha256
        self.idle_s = idle_s


class ChunkMismatchError(NectarRejectedError):
    """Raise when a later chunk of a deposit disagrees with its group's first on a declared field.

    Every chunk of one deposit repeats the whole deposit's metadata (docs/waggle/spec.md section
    8.7); a chunk that changes it mid-stream (a different total, kind, media type, title, label or
    provenance) would leave intake unable to say which version the reassembled content is, so the
    whole group is dropped instead of silently picking one.
    """

    code: ClassVar[str] = "hivemind.honey_store.chunk_mismatch"

    def __init__(self, sender: str, sha256: str, field: str) -> None:
        """Build the error for a chunk whose metadata contradicts its group's first chunk.

        Args:
            sender: The envelope sender id the group belongs to.
            sha256: The deposit's declared digest (the group key's content half).
            field: The name of the first `NectarDeposit` field found to differ.
        """
        super().__init__(
            f"A chunk of Nectar deposit {sha256} from {sender} disagrees with the group's first "
            f"chunk on {field}; the whole deposit was discarded."
        )
        self.sender = sender
        self.sha256 = sha256
        self.field = field


class CellMismatchError(NectarRejectedError):
    """Raise when a deposit names a Cell other than the one its relaying Warden supervises.

    A Warden relays only its own Cell's deposits; a chunk claiming another Cell would let one
    Cell's material be filed under another's provenance, clearance floor and tier, so intake
    trusts the Queen's own record of the sending Warden's Cell, never the chunk's claim.
    """

    code: ClassVar[str] = "hivemind.honey_store.cell_mismatch"

    def __init__(self, deposit_cell_id: str, source_cell_id: str) -> None:
        """Build the error for a deposit relayed from the wrong Cell.

        Args:
            deposit_cell_id: The Cell the deposit claims it was gathered on.
            source_cell_id: The Cell the Queen's record says the sending Warden supervises.
        """
        super().__init__(
            f"Nectar deposit claims Cell {deposit_cell_id} but arrived from the Warden of Cell "
            f"{source_cell_id}; a Warden relays only its own Cell's deposits."
        )
        self.deposit_cell_id = deposit_cell_id
        self.source_cell_id = source_cell_id


class DepositLengthMismatchError(NectarRejectedError):
    """Raise when a deposit's received bytes overrun, or on `final` fall short of, `total_bytes`.

    docs/waggle/spec.md section 5: the total a first chunk declares is checked against the cap
    before any byte is buffered, so a group may never grow past it (that is what keeps one open
    deposit's memory bounded), and the final chunk must bring the length to exactly that total.
    """

    code: ClassVar[str] = "hivemind.honey_store.deposit_length_mismatch"

    def __init__(self, sender: str, sha256: str, total_bytes: int, received_bytes: int) -> None:
        """Build the error for a deposit whose length disagrees with its declared total.

        Args:
            sender: The envelope sender id the group belongs to.
            sha256: The deposit's declared digest.
            total_bytes: The total its chunks declared.
            received_bytes: How many bytes the group held once this chunk was counted.
        """
        super().__init__(
            f"Nectar deposit {sha256} from {sender} declared {total_bytes} bytes but its chunks "
            f"came to {received_bytes}; the whole deposit was discarded."
        )
        self.sender = sender
        self.sha256 = sha256
        self.total_bytes = total_bytes
        self.received_bytes = received_bytes


class LabelLoweringError(HoneyStoreError):
    """Raise when a proposed clearance change is not a real lowering, or has no approver.

    codingrules 8.9: "only a judge verdict or a human may lower one [label]." A model may still
    raise a label freely (`raise_label`); this error exists only for the one direction that needs
    an approver, checked by `hivemind.honey_store.clearance.check_lowering`.
    """

    code: ClassVar[str] = "hivemind.honey_store.label_lowering_refused"

    def __init__(self, current: str, target: str, approver: str | None) -> None:
        """Build the error for a refused lowering.

        Args:
            current: The item's current clearance, as its `.name` (`"C2"`).
            target: The proposed clearance, as its `.name`.
            approver: The proposed approver's name (`"JUDGE"`, `"HUMAN"`), or None when no
                approver was given at all.
        """
        who = approver if approver is not None else "no approver"
        super().__init__(
            f"Cannot lower clearance from {current} to {target} ({who}): a lowering must rank "
            "strictly below the current label and carry a JUDGE or HUMAN approver."
        )
        self.current = current
        self.target = target
        self.approver = approver


class InvalidScopeError(HoneyStoreError):
    """Raise when a scope string does not match `waggle.messages.honey.hit.SCOPE_PATTERN`.

    Every Honey Store scope is exactly `hive`, `cell:<id>`, `bee:<id>` or `task:<id>`
    (docs/waggle/spec.md section 8.7); anything else -- a typo, a stray colon, a browser path
    fed back in as a scope -- is refused here rather than silently accepted as a new scope kind.
    """

    code: ClassVar[str] = "hivemind.honey_store.invalid_scope"

    def __init__(self, scope: str) -> None:
        """Build the error for a malformed scope.

        Args:
            scope: The offending string.
        """
        super().__init__(
            f"{scope!r} is not a valid Honey Store scope: must be 'hive', 'cell:<id>', "
            "'bee:<id>' or 'task:<id>' (waggle.messages.honey.hit.SCOPE_PATTERN)."
        )
        self.scope = scope
