"""Build a started Warden with a Basket, a sub-bee that may ask for tools, and a fake Queen.

Roadmap phase 9 (wave 3H) tests need a Warden that holds a Basket: the Hive Stand's (a REAL Cell,
the Lab's handles in `royal_jelly`) or a Virtual Cell's (its own store and node keys).
`make_tool_rig`
builds and starts one over a test's `tmp_path` and hands back a `ToolRig` with the Queen's raw end
of its link; `add_sub_bee` registers a sub-bee that may ask for tools; `QueenDouble` plays the
Queen's side of a Basket shipment with the real `TransferReceiver`; `make_resident` puts a package
in the Warden's store. Split from `builders.wardens` (which builds the deps and the `QueenEnd`)
for codingrules 5.1's size limit.

Fits into the Hive:
    Test infrastructure (codingrules section 14.5), not shipped. Used by the tests under
    packages/hivemind/tests/unit/wardens for the Royal Jelly and Basket tick handlers.

Key invariants:
    - Everything is built over a test's own directory and a FakeClock; nothing sleeps.
    - A rig's Warden is started and must be stopped by the test (the `hive_rig` and `cell_rig`
      fixtures do).

See Also:
    - builders.wardens for `make_warden_deps`, which every rig is built on.
    - hivemind.wardens.ticks.tools and .basket for what these rigs exercise.
"""

from __future__ import annotations

import asyncio
import dataclasses
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from builders.basket import (
    FakeHoneyPort,
    make_blob,
    make_kit,
    make_manifest,
    make_signer,
    open_test_basket_store,
)
from builders.cells import make_cell
from builders.honey_wire import WireEnd
from builders.wardens import make_warden_deps
from builders.workers import (
    ScriptedWorker,
    make_assignment,
    make_context,
    make_outcome,
    yield_then,
)

from hivemind.basket import (
    Ack,
    BasketStore,
    MediaKind,
    RefusalCode,
    Refuse,
    ResidencyDecision,
    ResidentBasket,
    TransferReceiver,
    ack_to_wire,
    chunk_from_wire,
    offer_from_wire,
    refuse_to_wire,
)
from hivemind.cell import Cell, CellKind, CombShieldLevel, HoneyClearance, OsFamily
from hivemind.cell.fake import FakeCellSource
from hivemind.cell.fake.source import FakeLeaseReleaser
from hivemind.cell.lease import LeaseFacts, LeaseRequest, RealCellLease
from hivemind.cell.source import CellIdentity
from hivemind.common.tasks import reap
from hivemind.guard import (
    Capability,
    CapabilityFamily,
    CapabilitySet,
)
from hivemind.llm import FakeLLMProvider
from hivemind.manifest.schema.royal_jelly import BasketSection, RoyalJellySection, SandboxKind
from hivemind.pheromone import PheromoneTrail
from hivemind.royal_jelly import InMemoryCombRegistry, QuarantineLimits, RoyalJellyAccess
from hivemind.wardens.inbox import to_inbox_item
from hivemind.wardens.links import QUEEN_LINK
from hivemind.wardens.spawn import SubBee
from hivemind.wardens.ticks.basket.handle import handle_basket_item
from hivemind.wardens.warden import Warden
from hivemind.workers.runtime import RuntimeDeps, WorkerRuntime
from hivemind.workers.state import WorkerState
from hivemind.workers.tools.comb import CombToolBundle
from waggle.clock import Clock, FakeClock
from waggle.codec import Codec
from waggle.envelope import Envelope, Hop
from waggle.ids import (
    CellId,
    new_cell_id,
    new_grant_id,
    new_hive_id,
    new_lease_id,
    new_warden_id,
    new_worker_id,
)
from waggle.messages.basket import BasketChunk, BasketOffer
from waggle.messages.forage import AllowedBinding, GrantIssued, SourceRef
from waggle.messages.forage.values import Effort as WireEffort
from waggle.signing import Ed25519Signer, Ed25519Verifier
from waggle.transport.memory import MemoryTransport

# ──────────────────────────────────────────────────────────────────────────────
# Roadmap phase 9 (wave 3H): a Warden with a Basket, a sub-bee that may ask for tools, a fake Queen
# ──────────────────────────────────────────────────────────────────────────────

__all__ = [
    "FakeResidency",
    "QueenDouble",
    "TmpScratchSource",
    "ToolRig",
    "add_sub_bee",
    "make_resident",
    "make_royal_jelly_access",
    "make_scaffolder_grant",
    "make_tool_rig",
]

_SUB_BEE_CAPABILITIES = ("tool:*", "tool:request", "net:example.org", "fs:read:**")


class TmpScratchSource(FakeCellSource):
    """A FakeCellSource whose leases have a real scratch directory under a test's `tmp_path`.

    The Warden spools a shipment's partial blob below its lease's scratch root, which the stock
    fake points at a path nobody may write; this source leases the same way with a real one.
    """

    def __init__(
        self,
        cells: Iterable[Cell],
        identity: CellIdentity,
        trail: PheromoneTrail,
        clock: Clock,
        scratch: Path,
    ) -> None:
        """Build the source over `cells`; every lease's scratch is a child of `scratch`."""
        super().__init__(cells, identity, trail, clock)
        self._scratch = scratch
        self._lease_identity, self._lease_trail, self._lease_clock = identity, trail, clock

    async def lease(self, request: LeaseRequest) -> RealCellLease:
        """Open a lease like the fake does, but with a scratch root that exists on disk."""
        cell = next(c for c in await self.cells() if c.id == request.cell_id)
        lease_id = new_lease_id(self._lease_clock)
        root = self._scratch / str(lease_id)
        root.mkdir(parents=True, exist_ok=True)
        facts = LeaseFacts(
            id=lease_id,
            cell_id=request.cell_id,
            holder=request.holder,
            task_id=request.task_id,
            scratch_root=root,
            access_level=request.access_level,
            comb_shield=cell.comb_shield,
            allowed_paths=request.allowed_paths,
        )
        lease = RealCellLease(
            facts,
            trail=self._lease_trail,
            clock=self._lease_clock,
            identity=self._lease_identity,
            releaser=FakeLeaseReleaser(),
        )
        await lease.open()
        return lease


@dataclass(frozen=True, slots=True)
class ToolRig:
    """A started Warden with a Basket, the Queen's raw end of its link and what a test inspects."""

    warden: Warden
    queen: WireEnd  # The Queen's end of the Warden's link: read what it sent, send as the Queen.
    clock: FakeClock
    provider: FakeLLMProvider  # Script a scaffold reply on it for a cell-scope request.
    access: RoyalJellyAccess | None  # The Hive Stand variant's handles; None for a Cell's.
    honey: FakeHoneyPort | None  # The Hive Stand door's Honey port: every record it was given.
    store: BasketStore  # Where this Warden's blobs land (the Hive's store, or its Cell's own).
    hive_signer: Ed25519Signer  # The Hive key a Cell's Warden trusts: sign a shipment with it.
    hive_signer_id: str
    cell_signer: Ed25519Signer  # The key a Cell's Warden signs what it ships up with.
    cell_signer_id: str


async def make_royal_jelly_access(
    root: Path, clock: Clock, cell_id: CellId, sandbox: SandboxKind = SandboxKind.SUBPROCESS
) -> tuple[RoyalJellyAccess, FakeHoneyPort]:
    """Build the Hive Stand's Royal Jelly handles over a real store and an in-memory registry.

    Returns:
        The handles, and the recording Honey port behind their deposit door.
    """
    kit = await make_kit(root)
    resident = ResidentBasket(kit.store, cell_id, 1 << 30, clock)
    access = RoyalJellyAccess(
        registry=InMemoryCombRegistry(clock),
        basket=kit.store,
        deposits=kit.deposits,
        resident=resident,
        signer=cast(Ed25519Signer, kit.wiring.signer),
        signer_id=kit.signer_id,
        verifier=kit.verifier,
        config=RoyalJellySection(sandbox=sandbox),
        basket_config=BasketSection(),
        limits=QuarantineLimits(),
        sandbox_kind=sandbox,
    )
    return access, kit.honey


@dataclass(frozen=True, slots=True)
class _Keys:
    """The keys a Cell's Warden is wired with: the one it trusts and the one it signs with."""

    hive_verifier: Ed25519Verifier
    cell_signer: Ed25519Signer
    cell_signer_id: str


async def _basket_parts(
    root: Path, cell: Cell, hive_stand: bool, wiring: tuple[SandboxKind, _Keys, Clock]
) -> tuple[RoyalJellyAccess | None, FakeHoneyPort | None, BasketStore, dict[str, object]]:
    """Build the store and the `WardenDeps` overrides that give a Warden its Basket."""
    sandbox, keys, clock = wiring
    if hive_stand:
        access, honey = await make_royal_jelly_access(root / "hive", clock, cell.id, sandbox)
        return (
            access,
            honey,
            access.basket,
            {
                "royal_jelly": access,
                "resident_basket": access.resident,
            },
        )
    store = await open_test_basket_store(root / "cell", clock=clock)
    return (
        None,
        None,
        store,
        {
            "resident_basket": ResidentBasket(store, cell.id, 1 << 30, clock),
            "basket_store": store,
            "node_signer": keys.cell_signer,
            "node_signer_id": keys.cell_signer_id,
            "hive_verifier": keys.hive_verifier,
            "lease_capability": Capability(family=CapabilityFamily.CELL_VIRTUAL),
        },
    )


async def make_tool_rig(
    root: Path,
    *,
    hive_stand: bool,
    sandbox: SandboxKind = SandboxKind.SUBPROCESS,
    comb_shield: CombShieldLevel = CombShieldLevel.MEADOW,
) -> ToolRig:
    """Build and start a Warden with a Basket: the Hive Stand's (`hive_stand`) or a Cell's own.

    Everything lives below `root` (a test's `tmp_path`); stop the Warden when done.
    """
    clock = FakeClock()
    kind = CellKind.REAL if hive_stand else CellKind.VIRTUAL
    cell = make_cell(kind=kind, clock=clock, comb_shield=comb_shield)
    provider = FakeLLMProvider(name="fake-warden")
    hive_id, warden_id = new_hive_id(clock), new_warden_id(clock)
    queen_transport, warden_transport = MemoryTransport.pair(Codec(), Codec())
    deps, _queen_end, _ = make_warden_deps(
        clock,
        cells=(cell,),
        fake_provider=provider,
        warden_id=warden_id,
        queen_link=warden_transport,
    )
    hive_signer, hive_signer_id, hive_verifier = make_signer(clock)
    cell_signer, cell_signer_id, _ = make_signer(clock)
    keys = _Keys(hive_verifier, cell_signer, cell_signer_id)
    access, honey, store, extra = await _basket_parts(
        root, cell, hive_stand, (sandbox, keys, clock)
    )
    ids = deps.identity
    identity = CellIdentity(hive_id=ids.hive_id, node_id=ids.node_id, actor="system")
    source = TmpScratchSource((cell,), identity, deps.trail, clock, root / "scratch")
    deps = dataclasses.replace(deps, source=source, **extra)  # type: ignore[arg-type]
    queen = WireEnd(queen_transport, Hop(hive_id, warden_id, deps.identity.node_id), clock)
    warden = Warden(warden_id, deps)
    await warden.start()
    return ToolRig(
        warden,
        queen,
        clock,
        provider,
        access,
        honey,
        store,
        hive_signer,
        hive_signer_id,
        cell_signer,
        cell_signer_id,
    )


def make_scaffolder_grant(warden: Warden, clock: Clock) -> GrantIssued:
    """Build a grant naming this Warden and Cell that allows the WORKER and SCAFFOLDER slots."""
    source = SourceRef(source_id="local", provider="fake", model="test-model", host_cell_id=None)
    cell = warden._cell
    return GrantIssued(
        grant_id=new_grant_id(clock),
        holder=warden._warden_id,
        cell_id=cell.id if cell is not None else new_cell_id(clock),
        task_id=None,
        revision=0,
        allowed=tuple(
            AllowedBinding(slot=slot, source=source, max_effort=WireEffort.MEDIUM)
            for slot in ("WORKER", "SCAFFOLDER")
        ),
        seats=(),
        token_budget=500_000,
        spend_budget=5.0,
        tokens_spent=0,
        spent=0.0,
        max_sub_bees=2,
        expires_at=clock.now(),
        reason="test grant",
    )


async def add_sub_bee(
    warden: Warden, clock: Clock, *, capabilities: Iterable[str] = _SUB_BEE_CAPABILITIES
) -> tuple[SubBee, WireEnd]:
    """Register one sub-bee that may ask for tools, over a real pair; its runtime never runs.

    The Warden also gets a grant that allows the SCAFFOLDER slot, which the sub-bee's assignment
    names, so a CELL-scope request finds a scaffolder binding to use.

    Returns:
        The sub-bee's row (with an empty Comb bundle) and its own raw end of its link.
    """
    grant = make_scaffolder_grant(warden, clock)
    warden._grants[grant.grant_id] = grant
    worker_id = new_worker_id(clock)
    link, worker_transport = MemoryTransport.pair(Codec(), Codec())
    hop = Hop(sender=worker_id, recipient=warden._warden_id, node_id=warden._deps.hop.node_id)
    bundle = CombToolBundle()
    runtime = WorkerRuntime(
        make_context(clock=clock, worker_id=worker_id, comb_tools=bundle),
        ScriptedWorker(yield_then(make_outcome)),
        RuntimeDeps(transport=worker_transport, hop=hop, heartbeat_interval_s=5.0, clock=clock),
    )
    done: asyncio.Task[None] = asyncio.ensure_future(asyncio.sleep(0))
    await done
    cell = warden._cell
    assert cell is not None
    assignment = make_assignment(clock=clock, cell_id=cell.id, grant_id=grant.grant_id)
    sub_bee = SubBee(
        worker_id=worker_id,
        task_id=assignment.task_id,
        assignment=assignment,
        attempt=1,
        state=WorkerState.RUNNING,
        binding="worker",
        last_handoff=None,
        link=link,
        runtime=runtime,
        runtime_task=done,
        capabilities=CapabilitySet.parse(*capabilities),
        comb_tools=bundle,
    )
    warden._sub_bees[worker_id] = sub_bee
    return sub_bee, WireEnd(worker_transport, hop, clock)


class FakeResidency:
    """A `ResidencyPlanner` that always has room: the Queen double's side of a shipment."""

    async def plan(self, size_bytes: int) -> ResidencyDecision:
        """Admit any blob."""
        return ResidencyDecision(True, (), f"{size_bytes} bytes fit.")


class QueenDouble:
    """The Queen's side of a Warden's Basket traffic, answering with the real state machines.

    Reads what the Warden sends up its link; a `basket.offer` or `basket.chunk` is answered by a
    real `TransferReceiver` (verifying hash and signature) and the answer is fed straight into the
    Warden's Basket handler, as a tick would; anything else is returned to the test.
    """

    def __init__(
        self, rig: ToolRig, verifier: Ed25519Verifier, spool: Path, refuse: bool = False
    ) -> None:
        """Bind the double to the rig's Warden and the verifier that trusts the Cell's key.

        Args:
            rig: The rig whose Queen link this double reads.
            verifier: Trusts the key the Warden signs the manifests it ships up with.
            spool: Where the double's receiver spools a partial blob.
            refuse: True to refuse every offer as a Queen over her ceiling would.
        """
        self._rig = rig
        self._verifier = verifier
        self._spool = spool
        self._refuse = refuse
        self._receivers: dict[str, TransferReceiver] = {}
        self.received: dict[str, bytes] = {}  # Every blob fully received and verified.
        self.offers: list[BasketOffer] = []  # Every offer seen, in order.
        self.other: list[Envelope] = []  # Everything that was not Basket traffic, in order.

    async def next_other(self) -> Envelope:
        """Answer Basket messages until one arrives that is not the Basket's; return that one."""
        while True:
            envelope = await self._rig.queen.next()
            payload = envelope.payload
            if isinstance(payload, BasketOffer):
                await self._on_offer(envelope, payload)
            elif isinstance(payload, BasketChunk):
                await self._on_chunk(envelope, payload)
            else:
                return envelope

    async def serve(self, job: asyncio.Future[Any]) -> None:
        """Answer Basket traffic until `job` ends; everything else is kept in `other`."""
        while not job.done():
            reader = asyncio.ensure_future(self._rig.queen.next())
            await asyncio.wait({reader, job}, return_when=asyncio.FIRST_COMPLETED)
            if not reader.done():
                await reap(reader)
                break
            envelope = reader.result()
            payload = envelope.payload
            if isinstance(payload, BasketOffer):
                await self._on_offer(envelope, payload)
            elif isinstance(payload, BasketChunk):
                await self._on_chunk(envelope, payload)
            else:
                self.other.append(envelope)

    async def _on_offer(self, envelope: Envelope, message: BasketOffer) -> None:
        """Answer an offer: refuse it, or let a receiver judge it."""
        self.offers.append(message)
        offer = offer_from_wire(message)
        receiver = self._receivers.setdefault(offer.sha256, TransferReceiver(self._spool))
        answer: Ack | Refuse
        if self._refuse:
            answer = Refuse(sha256=offer.sha256, code=RefusalCode.CLEARANCE, reason="too high")
        else:
            answer = await receiver.on_offer(
                offer, self._verifier, HoneyClearance.C2, FakeResidency(), OsFamily.LINUX
            )
        await self._reply(envelope, answer)

    async def _on_chunk(self, envelope: Envelope, message: BasketChunk) -> None:
        """Feed a chunk to its receiver and answer; keep the bytes once the blob completes."""
        receiver = self._receivers[message.sha256]
        answer = await receiver.on_chunk(chunk_from_wire(message))
        if isinstance(answer, Ack) and answer.complete and receiver.completed_path is not None:
            self.received[message.sha256] = receiver.completed_path.read_bytes()
        await self._reply(envelope, answer)

    async def _reply(self, request: Envelope, answer: Ack | Refuse) -> None:
        """Wrap the answer as a reply and hand it to the Warden's Basket handler."""
        payload = refuse_to_wire(answer) if isinstance(answer, Refuse) else ack_to_wire(answer)
        reply = self._rig.queen.envelope_for(payload, correlation_id=request.id)
        await handle_basket_item(self._rig.warden, to_inbox_item(reply, QUEEN_LINK))


async def make_resident(rig: ToolRig, content: bytes) -> str:
    """Store `content` as a tool-package blob in the rig's store and make it resident."""
    cell = rig.warden._cell
    assert cell is not None
    blob = make_blob(
        content,
        media_kind=MediaKind.TOOL_PACKAGE,
        media_type="application/zip",
        origin_cell_id=cell.id,
    )
    manifest = make_manifest(blob, signer=rig.cell_signer, signer_id=rig.cell_signer_id)
    stored, _is_new = await rig.store.deposit(content, blob, manifest)
    resident = rig.warden._deps.resident_basket
    assert resident is not None
    await resident.admit(stored)
    return stored.sha256
