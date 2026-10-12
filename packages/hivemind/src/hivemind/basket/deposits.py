"""Take a blob into the Basket: cap, label, scan, sign, store and describe it in Honey.

`BasketDeposits` is the one door every blob comes through (ADR-0048), the counterpart of
`hivemind.honey_store.nectar.NectarIntake`. A Warden hands it a `DepositRequest` (the bytes plus
the title and summary its bee wrote and the provenance the Warden knows). The door refuses a blob
over `max_blob_bytes`, labels it (`blob_clearance`: the declared label raised to the provenance
floor, and refused if a Night Veil Cell would export above C1), scans text-like content with the
Guard's untrusted-content scanner and refuses a flagged one, signs the blob's manifest with the Hive
key, stores it deduplicating by hash, and writes exactly one Honey record for a new blob through a
port: that record is the only way a bee ever finds the blob. The door holds no trail; the events it
builds are returned in `DepositOutcome.events` (or attached to the error it raises) for the caller
to record, as the Hive Stand Warden's deposit path does. The one trail it indirectly touches is the
scanner's own: `ContentScanner.scan` records `guard.injection_suspected` on the `ScanRecorder` it is
given, before it returns a flag.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy), inside `hivemind.basket`. Called by the
    Hive Stand Warden's Basket tick and by the Queen when a Warden ships a deposit up. Calls into
    `hivemind.basket` (models, clearance, manifest, events, store), `hivemind.guard.scanner` and
    waggle (signing protocol, clock, ids); the Honey Store arrives only through `HoneyRecordPort`.

Key invariants:
    - A blob over `max_blob_bytes`, flagged by the scanner, or above a Night Veil Cell's ceiling is
      never stored and never gets a Honey record; each refusal raises with its
      `basket.deposit_refused` event attached.
    - A new blob gets exactly one Honey record; a repeat deposit of the same bytes gets none and
      reports `basket.deposit_deduplicated`. A repeat may only raise the stored label.
    - The Honey record is written before the store so a failure never leaves a blob nobody can
      find; Honey itself dedupes a retried record by its `source_key`.
    - Blob bytes, titles and summaries never appear in an event or an error message.
    - Text is scanned only up to the scanner's bound; any reader re-screens what it shows a model
      (`workers.tools.screen`), so the unscanned tail never reaches one.

See Also:
    - docs/adr/0048-basket-inert-blobs-residency-and-transfer.md for the decisions followed.
    - hivemind.honey_store.nectar.intake for the door this mirrors.
    - hivemind.guard.scanner for ContentScanner.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from pydantic import JsonValue

from hivemind.basket.clearance import blob_clearance, receiver_ceiling
from hivemind.basket.errors import (
    BlobNotFoundError,
    BlobRefusedError,
    BlobTooLargeError,
    RefusalReason,
)
from hivemind.basket.events import EventStamp, basket_event, blob_facts
from hivemind.basket.manifest import BlobManifest
from hivemind.basket.models import Blob, DepositRequest, HoneyRecord, HoneyRecordPort, MediaKind
from hivemind.basket.store import BasketStore
from hivemind.cell import HoneyClearance
from hivemind.guard.scanner import ContentScanner, ScanRecorder, ScanSite, ScanSource
from hivemind.pheromone import PheromoneEvent
from waggle.clock import Clock
from waggle.codec import Signer
from waggle.ids import new_event_id

SHA256_PREFIX_CHARS = 12  # Enough of a digest to tell blobs apart in a scan locator, never more.

__all__ = ["BasketDeposits", "DepositOutcome", "DepositWiring"]


@dataclass(frozen=True, slots=True)
class DepositWiring:
    """The collaborators and limits the door needs beyond its store, Honey port and clock."""

    scanner: ContentScanner  # Reads text-like content before it is kept.
    recorder: ScanRecorder  # Where the scanner records a flag; its identity stamps our events.
    signer: Signer  # The Hive key's signer (`waggle.signing.Ed25519Signer`).
    signer_id: str  # The node id a verifier looks the Hive key up under.
    max_blob_bytes: int  # `[basket] max_blob_bytes`: the largest blob this door accepts.


@dataclass(frozen=True, slots=True)
class DepositOutcome:
    """What a deposit produced: the blob, whether it was new and the events to record."""

    blob: Blob  # The stored blob (an existing one, label possibly raised, on a repeat).
    is_new: bool  # True only when this call created the blob.
    events: tuple[PheromoneEvent, ...]  # `basket.deposited` or `basket.deposit_deduplicated`.


class BasketDeposits:
    """The one door into the Basket: cap, label, scan, sign, store and describe every blob."""

    def __init__(
        self, store: BasketStore, honey: HoneyRecordPort, wiring: DepositWiring, clock: Clock
    ) -> None:
        """Wire the door to its store, its Honey port, its collaborators and its clock.

        Args:
            store: Where blobs and their manifests land.
            honey: Where a new blob's one Honey record is written.
            wiring: The scanner, recorder, signer and size cap.
            clock: Stamps `deposited_at` and the events.
        """
        self._store = store
        self._honey = honey
        self._wiring = wiring
        self._clock = clock
        self._stamp = EventStamp(wiring.recorder.identity, clock)

    async def deposit(self, request: DepositRequest) -> DepositOutcome:
        """Take one blob in, or refuse it; see the module docstring for the order of checks.

        Args:
            request: The bytes, their Honey text and provenance.

        Returns:
            The stored blob, whether it was new, and the events for the caller to record.

        Raises:
            BlobTooLargeError: Over `max_blob_bytes`; carries a `basket.deposit_refused` event.
            BlobRefusedError: `clearance` (a Night Veil Cell exporting above C1) or `scanner`
                (flagged text); carries a `basket.deposit_refused` event.
        """
        size = len(request.content)
        if size > self._wiring.max_blob_bytes:
            raise BlobTooLargeError(
                size, self._wiring.max_blob_bytes, (self._refused(request, "size", None),)
            )
        sha256 = hashlib.sha256(request.content).hexdigest()
        label = blob_clearance(request.declared, request.from_borrowed_cell, request.tier)
        # ADR-0035's Night Veil rule: that tier exports nothing above C1, so a label above it can
        # only mean Royal data is about to cross the boundary that exists to contain it.
        if label.rank > receiver_ceiling(request.tier).rank:
            raise self._refusal(
                request, sha256, "clearance", f"{label.value} from a Night Veil Cell"
            )
        await self._screen(request, sha256)
        existing = await self._existing(sha256)
        if existing is not None:
            return await self._dedupe(request, existing, label)
        return await self._add(request, sha256, label)

    async def _existing(self, sha256: str) -> Blob | None:
        """Return the stored blob for this hash, or None when the Basket has never held it."""
        try:
            return await self._store.get(sha256)
        except BlobNotFoundError:
            return None

    async def _screen(self, request: DepositRequest, sha256: str) -> None:
        """Scan text-like content and refuse a flagged one."""
        text = _scannable_text(request)
        if text is None:
            return
        # A deposit is the Basket's nectar: it reads at the same seam and under the same tiers.
        site = ScanSite(
            source=ScanSource.NECTAR_INTAKE,
            consumer=request.origin_bee or self._wiring.recorder.identity.hive_id,
            recorder=self._wiring.recorder,
            tier=request.tier,
            task_id=request.origin_task_id,
            ref=f"basket:{sha256[:SHA256_PREFIX_CHARS]}",
        )
        # Local, CPU-bound and bounded by the scanner's own limit; on a flag it also writes one
        # `guard.injection_suspected` event to the recorder's trail before returning.
        verdict = await self._wiring.scanner.scan(text, site)
        if verdict.flagged:
            raise self._refusal(
                request, sha256, "scanner", f"flagged ({', '.join(verdict.families)})"
            )

    async def _add(
        self, request: DepositRequest, sha256: str, label: HoneyClearance
    ) -> DepositOutcome:
        """Store a blob the Basket has not held and describe it in Honey."""
        event_id = new_event_id(self._clock)
        blob = Blob(
            sha256=sha256,
            size_bytes=len(request.content),
            media_kind=request.media_kind,
            media_type=request.media_type,
            platform_tags=request.platform_tags,
            clearance=label,
            origin_cell_id=request.origin_cell_id,
            origin_task_id=request.origin_task_id,
            origin_bee=request.origin_bee,
            deposited_at=self._clock.now(),
            deposit_event_id=event_id,
            tainted=request.tainted,
        )
        manifest = BlobManifest.sign(blob, self._wiring.signer_id, self._wiring.signer)
        # Honey first: if the store then fails nothing is lost (a retry writes the same record,
        # which Honey dedupes), whereas the reverse order could keep a blob nobody can find.
        await self._honey.record(_honey_record(request, blob))
        stored, is_new = await self._store.deposit(request.content, blob, manifest)
        if not is_new:
            # Lost a race with a concurrent deposit of the same bytes: report the dedupe it is.
            return self._deduplicated(stored, label_raised=False)
        event = basket_event(
            self._stamp,
            "basket.deposited",
            request.origin_cell_id,
            blob_facts(stored),
            event_id=event_id,
        )
        return DepositOutcome(blob=stored, is_new=True, events=(event,))

    async def _dedupe(
        self, request: DepositRequest, existing: Blob, label: HoneyClearance
    ) -> DepositOutcome:
        """Report a repeat deposit, raising the stored label if this one is more sensitive."""
        higher = label.rank > existing.clearance.rank
        newly_tainted = request.tainted and not existing.tainted
        if not (higher or newly_tainted):
            return self._deduplicated(existing, label_raised=False)
        # Same bytes from a more sensitive place must never be handled as less sensitive: raise
        # the label (and taint) and re-sign, since the signature covers both.
        merged = existing.model_copy(
            update={
                "clearance": label if higher else existing.clearance,
                "tainted": existing.tainted or request.tainted,
            }
        )
        manifest = BlobManifest.sign(merged, self._wiring.signer_id, self._wiring.signer)
        stored, _ = await self._store.deposit(request.content, merged, manifest)
        return self._deduplicated(stored, label_raised=True)

    def _deduplicated(self, blob: Blob, *, label_raised: bool) -> DepositOutcome:
        """Build the outcome for a deposit that matched a blob the Basket already held."""
        facts = blob_facts(blob) | {"label_raised": label_raised}
        event = basket_event(
            self._stamp,
            "basket.deposit_deduplicated",
            blob.origin_cell_id,
            facts,
        )
        return DepositOutcome(blob=blob, is_new=False, events=(event,))

    def _refused(self, request: DepositRequest, code: str, sha256: str | None) -> PheromoneEvent:
        """Build the `basket.deposit_refused` event: the code, size and hash, never the content."""
        payload: dict[str, JsonValue] = {
            "code": code,
            "size_bytes": len(request.content),
            "sha256": sha256,
            "media_kind": request.media_kind.value,
        }
        return basket_event(
            self._stamp,
            "basket.deposit_refused",
            request.origin_cell_id,
            payload,
        )

    def _refusal(
        self, request: DepositRequest, sha256: str, reason: RefusalReason, detail: str
    ) -> BlobRefusedError:
        """Build the refusal error for a deposit, with its trail event attached."""
        return BlobRefusedError(
            reason,
            f"deposit of blob {sha256} was refused: {detail}.",
            (self._refused(request, reason, sha256),),
        )


def _honey_record(request: DepositRequest, blob: Blob) -> HoneyRecord:
    """Build the one Honey record a new blob gets: the bee's text plus the blob's own facts."""
    return HoneyRecord(
        title=request.title,
        summary=request.summary,
        sha256=blob.sha256,
        media_kind=blob.media_kind,
        platform_tags=blob.platform_tags,
        clearance=blob.clearance,
        origin_cell_id=blob.origin_cell_id,
        origin_task_id=blob.origin_task_id,
        origin_bee=blob.origin_bee,
        deposited_at=blob.deposited_at,
        deposit_event_id=blob.deposit_event_id,
    )


def _scannable_text(request: DepositRequest) -> str | None:
    """Return the text the scanner should read, or None when the bytes are not text-like."""
    if request.media_type.startswith("text/") or request.media_kind is MediaKind.TEXT:
        # Declared text: decode leniently so a stray byte cannot hide the rest from the scanner.
        return request.content.decode("utf-8", errors="replace")
    if request.media_kind is MediaKind.TOOL_PACKAGE:
        # NOTE: a tool package is an archive; we do not unzip it here (that is the Comb's job, in
        # its sandbox). Scan it only if it happens to be plain text, otherwise skip: the Comb's
        # static gate, not this scan, is what stands between a package and execution.
        try:
            return request.content.decode("utf-8")
        except UnicodeDecodeError:
            return None
    # A binary cannot be scanned and is deposited as is: a blob is inert (ADR-0048).
    return None
