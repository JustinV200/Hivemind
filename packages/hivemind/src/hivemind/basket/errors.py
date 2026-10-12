"""Define the Basket's error tree: a missing blob, a refused deposit, a failed check.

The Basket is the Hive's store for the bytes work produces and Honey cannot hold (a wheel, a
dataset, a tool package). Every subsystem roots its errors in `hivemind.common.errors.
HiveMindError` (codingrules section 10); this module is that root for `hivemind.basket`.
`BlobRefusedError` and `TransferError` each carry a closed `reason_code` string rather than being a
family of subclasses, because the reasons are data the Warden maps onto a Waggle `basket.refuse`
code and a Pheromone Trail payload, and `HiveMindError.code` is a `ClassVar` that mypy --strict will
not let an instance override. Every error may carry the Pheromone Trail events the failing door
built (`events`), because a door that never holds a trail (`BasketDeposits`) still owes the caller
the `basket.deposit_refused` record of what it refused.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy). Raised by `hivemind.basket.store`
    (not-found, integrity, retired), `hivemind.basket.deposits` (size, scanner, clearance),
    `hivemind.basket.manifest` (signature checks) and `hivemind.basket.transfer` (protocol
    misuse). Calls into `hivemind.common.errors` and `hivemind.pheromone` (event typing) only.

Key invariants:
    - Every class sets its own `code`; a caller matches on `code` (and `reason_code` for the two
      reason-carrying classes), never on the message text.
    - A message names hashes, sizes and ids only; never a blob's bytes or a deposit's title.
    - `reason_code` is always one of the module's closed vocabularies below.

See Also:
    - docs/adr/0048-basket-inert-blobs-residency-and-transfer.md for the refusal reasons.
    - .claude/codingrules.md section 10 for the errors rules this module follows.
"""

from __future__ import annotations

from typing import ClassVar, Literal

from hivemind.common.errors import HiveMindError, NotFoundError
from hivemind.pheromone import PheromoneEvent

__all__ = [
    "REFUSAL_REASONS",
    "TRANSFER_FAILURES",
    "BasketError",
    "BlobIntegrityError",
    "BlobNotFoundError",
    "BlobRefusedError",
    "BlobTooLargeError",
    "RefusalReason",
    "ShipmentNotFoundError",
    "TransferError",
    "TransferFailure",
]

# Why a deposit, a read or an offered blob is refused (ADR-0048); each maps to one Waggle refusal.
type RefusalReason = Literal[
    "scanner",
    "clearance",
    "tainted",
    "no_room",
    "unknown_signer",
    "integrity",
    "platform",
    "retired",
]
# Why a transfer step is a protocol error rather than a refusal: the peer broke the sequence.
type TransferFailure = Literal["offset", "complete", "unknown", "too_large"]

REFUSAL_REASONS: frozenset[str] = frozenset(
    {
        "scanner",
        "clearance",
        "tainted",
        "no_room",
        "unknown_signer",
        "integrity",
        "platform",
        "retired",
    }
)  # The runtime copy of RefusalReason, so a test can prove the two never drift apart.
TRANSFER_FAILURES: frozenset[str] = frozenset({"offset", "complete", "unknown", "too_large"})


class BasketError(HiveMindError):
    """Root of every error `hivemind.basket` raises on purpose.

    Attributes:
        events: Trail events the raising door built about this failure, for its caller to record;
            empty for the many errors raised below a door that records nothing.
    """

    code: ClassVar[str] = "hivemind.basket.error"

    def __init__(self, message: str, events: tuple[PheromoneEvent, ...] = ()) -> None:
        """Build the error.

        Args:
            message: A full sentence naming the identifiers needed to debug, never blob bytes.
            events: Pheromone Trail events the caller should record because this was raised.
        """
        super().__init__(message)
        self.events = events


class BlobNotFoundError(BasketError, NotFoundError):
    """Raise when a lookup by sha256 finds no blob row (or the sha256 is not a digest at all)."""

    code: ClassVar[str] = "hivemind.basket.blob_not_found"

    def __init__(self, sha256: str) -> None:
        """Build the error for a missing blob.

        Args:
            sha256: The digest that was looked up and not found.
        """
        super().__init__(f"No blob with sha256 {sha256!r} exists in the Basket.")
        self.sha256 = sha256


class ShipmentNotFoundError(BasketError, NotFoundError):
    """Raise when a shipment id names no row in the queue."""

    code: ClassVar[str] = "hivemind.basket.shipment_not_found"

    def __init__(self, shipment_id: int) -> None:
        """Build the error for a missing shipment.

        Args:
            shipment_id: The queue id that was looked up and not found.
        """
        super().__init__(f"No shipment with id {shipment_id} exists in the Basket's queue.")
        self.shipment_id = shipment_id


class BlobTooLargeError(BasketError):
    """Raise when a deposit is over the size a door or a store allows."""

    code: ClassVar[str] = "hivemind.basket.blob_too_large"

    def __init__(
        self, size_bytes: int, max_bytes: int, events: tuple[PheromoneEvent, ...] = ()
    ) -> None:
        """Build the error for an oversized blob.

        Args:
            size_bytes: The size the deposit has.
            max_bytes: The cap it exceeded (`[basket] max_blob_bytes`).
            events: Trail events for the caller to record.
        """
        super().__init__(
            f"Blob is {size_bytes} bytes, over the {max_bytes}-byte cap ([basket] max_blob_bytes).",
            events,
        )
        self.size_bytes = size_bytes
        self.max_bytes = max_bytes


class BlobRefusedError(BasketError):
    """Raise when a blob is refused by rule: a deposit, a read of a retired blob, an offer.

    The `reason_code` is the machine-readable reason; the sentence says which blob and why.
    """

    code: ClassVar[str] = "hivemind.basket.blob_refused"

    def __init__(
        self,
        reason_code: RefusalReason,
        detail: str,
        events: tuple[PheromoneEvent, ...] = (),
    ) -> None:
        """Build the error for a refused blob.

        Args:
            reason_code: One of `REFUSAL_REASONS`.
            detail: A full sentence naming the blob (by hash) and the rule, never its bytes.
            events: Trail events for the caller to record (`basket.deposit_refused`).
        """
        super().__init__(f"Blob refused ({reason_code}): {detail}", events)
        self.reason_code = reason_code


class BlobIntegrityError(BasketError):
    """Raise when a blob's bytes or manifest do not match the hash or signature that names them."""

    code: ClassVar[str] = "hivemind.basket.blob_integrity"

    def __init__(self, sha256: str, detail: str) -> None:
        """Build the error for a failed integrity check.

        Args:
            sha256: The digest the blob is supposed to have.
            detail: Which check failed (hash, size, signature, missing file), never the bytes.
        """
        super().__init__(f"Blob {sha256} failed its integrity check: {detail}")
        self.sha256 = sha256


class TransferError(BasketError):
    """Raise when a peer breaks the transfer sequence: a gap, a chunk after the end, a stranger.

    A refusal by rule is a `Refuse` value on the wire, not this error; this is the protocol
    itself being misused, which the wire layer answers with an error message.
    """

    code: ClassVar[str] = "hivemind.basket.transfer"

    def __init__(self, reason_code: TransferFailure, detail: str) -> None:
        """Build the error for a transfer step out of sequence.

        Args:
            reason_code: One of `TRANSFER_FAILURES`.
            detail: A full sentence naming the transfer (by hash) and what was wrong.
        """
        super().__init__(f"Transfer failed ({reason_code}): {detail}")
        self.reason_code = reason_code
