"""Give a Warden its way to deposit a blob: the node keys, the deposit door and the Honey record.

Every blob enters the Basket through one door (`BasketDeposits`: cap, label, scan, sign, store,
describe). On the Hive Stand that door is the Hive's own (`RoyalJellyAccess.deposits`), signing
with the Hive key. A Cell's Warden has no Hive key and no Honey Store, so it builds a door of its
own over its resident store, signs manifests with its node key (the one the Queen already trusts
for its frames) and leaves the Honey record to the Queen: a Cell has no Honey Store, and she
writes the one record every deposit needs when she re-deposits the blob a Cell ships up (the
offer's reason becomes its summary), so `DeferredHoneyPort` writes nothing. `node_keys`
resolves, for either kind of Warden, the signer, the id its public key is registered under and the
verifier for the manifests it receives; `send_nectar` is the one place this Warden turns whole
content into `NectarDeposit` chunks and sends them up.

Fits into the Hive:
    Layer 5 (per-Cell supervisors; spawn and supervise Workers), inside the wardens package's
    ticks.basket sub-package. Called by `retain`, `receive`, `send` and the tool handlers in
    `hivemind.wardens.ticks.tools`. Calls into `hivemind.basket`, `hivemind.guard.scanner`,
    `hivemind.manifest` (the documented default size cap), `hivemind.workers.nectar` and waggle.

Key invariants:
    - A Hive Stand Warden always uses the Hive's door; a Cell's Warden builds its own once and
      keeps it.
    - A Cell's blob has its Honey record written by the Queen when it is shipped up; a blob that is
      never shipped (a tainted one) is never described, so nobody can find it.
    - Nectar sent from here never carries a blob's bytes.

See Also:
    - docs/adr/0048-basket-inert-blobs-residency-and-transfer.md ("Honey describes").
    - hivemind.basket.deposits for the door itself.
    - hivemind.workers.nectar for the chunking this reuses.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from hivemind.basket import BasketDeposits, DepositWiring, HoneyRecord
from hivemind.guard.scanner import ScanRecorder
from hivemind.manifest.schema.royal_jelly import DEFAULT_MAX_BLOB_BYTES
from hivemind.wardens.links import send_guarded
from hivemind.wardens.ticks.basket.events import cell_identity
from hivemind.wardens.ticks.basket.source import store_of
from hivemind.workers.nectar import DepositMeta, split_deposit
from waggle.codec import Signer, Verifier
from waggle.envelope import wrap

if TYPE_CHECKING:
    from hivemind.wardens.deps import WardenDeps
    from hivemind.wardens.warden import Warden

__all__ = [
    "DeferredHoneyPort",
    "NodeKeys",
    "deposit_door",
    "node_keys",
    "send_nectar",
]


@dataclass(frozen=True, slots=True)
class NodeKeys:
    """The signing facts a Warden needs to sign what it ships and verify what it receives."""

    signer: Signer  # Signs the manifest of a blob this Warden deposits.
    signer_id: str  # The node id the matching public key is registered under at the receiver.
    verifier: Verifier  # Trusts the keys that sign the manifests this Warden receives.


def node_keys(deps: WardenDeps) -> NodeKeys | None:
    """Return this Warden's signing facts: its own node's, or the Hive Stand's Hive key.

    Args:
        deps: The Warden's collaborators.

    Returns:
        The keys, or None when this Warden holds neither (it then deposits and receives nothing).
    """
    if deps.node_signer is not None and deps.node_signer_id and deps.hive_verifier is not None:
        return NodeKeys(deps.node_signer, deps.node_signer_id, deps.hive_verifier)
    access = deps.royal_jelly
    if access is not None:
        return NodeKeys(access.signer, access.signer_id, access.verifier)
    return None


def deposit_door(warden: Warden) -> BasketDeposits | None:
    """Return the door this Warden deposits through, building a Cell's own on first use.

    Args:
        warden: The depositing Warden.

    Returns:
        The Hive's door on the Hive Stand; otherwise a door over this Cell's own store, signing
        with its node key. None when it has no store or no key (nothing can be deposited).
    """
    deps = warden._deps
    if deps.royal_jelly is not None:
        return deps.royal_jelly.deposits
    if warden._basket.door is not None:
        return warden._basket.door
    store, keys = store_of(deps), node_keys(deps)
    if store is None or keys is None:
        return None
    wiring = DepositWiring(
        scanner=deps.scanner,
        recorder=ScanRecorder(trail=deps.trail, identity=cell_identity(deps), clock=deps.clock),
        signer=keys.signer,
        signer_id=keys.signer_id,
        max_blob_bytes=DEFAULT_MAX_BLOB_BYTES,
    )
    warden._basket.door = BasketDeposits(store, DeferredHoneyPort(), wiring, deps.clock)
    return warden._basket.door


async def send_nectar(warden: Warden, content: bytes, meta: DepositMeta) -> bool:
    """Send `content` up to the Queen as Nectar chunks sharing `meta`; False if one was lost.

    Args:
        warden: The sending Warden.
        content: The whole deposit, at least one byte.
        meta: What the deposit is and where it came from.

    Returns:
        True when every chunk reached the Queen's link; False as soon as one did not (an
        incomplete group is discarded by intake once it goes idle).
    """
    for chunk in split_deposit(content, meta):
        # One fresh envelope per chunk: the Queen answers only a refusal, never a chunk.
        envelope = wrap(chunk, warden._deps.hop, clock=warden._deps.clock)
        if not await send_guarded(warden._deps.queen_link, envelope):
            return False
    return True


class DeferredHoneyPort:
    """A `HoneyRecordPort` that writes nothing: the Queen describes the blob when it reaches her."""

    async def record(self, record: HoneyRecord) -> None:
        """Accept the record and leave its writing to the Queen's re-deposit of the shipped blob.

        Args:
            record: The title, summary, hash and provenance the door wrote; the summary travels
                as the shipment's reason.
        """
        del record  # Nothing to do here: see the class docstring.
