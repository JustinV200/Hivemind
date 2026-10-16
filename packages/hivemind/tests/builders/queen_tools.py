"""Build the Queen's tool rig: a Queen with a Royal Jelly Lab, a Basket, Wardens and a fake sandbox.

The Queen's tool handlers (`hivemind.queen.ticks.tools`) are driven by hand in the unit tests, the
way `test_honey.py` drives the Honey handlers: an `InboxItem` goes into `handle_tool_item`, the
long work runs as a job in the tool lane, and a Warden's end of the link is read for the answer.
`ToolRig` is that setup: a real Basket on a temp directory, an in-memory Comb Registry, the scripted
`FakeScaffolder` and one attached Warden. `FakeWarden` is that Warden's other end: it keeps the
blobs the Queen ships with the real `TransferReceiver`, answers a Comb order with a canned report,
and hands everything else (a `tool.promoted`, a `control.error`) to the test through `inbox`; each
reply it makes is fed to `handle_tool_item` exactly as the Queen's tick would deliver it.
`FakeSandboxProvider` hands out sandbox Cells whose Warden is a `FakeWarden`.

Fits into the Hive:
    Test infrastructure (codingrules section 14.5), not shipped. Used by the tests under
    packages/hivemind/tests/unit/queen/ticks/tools.

Key invariants:
    - The Hive key signs every manifest the Queen ships and a `FakeWarden` trusts exactly that key.
    - Every read has a real-time bound, so a message that never comes fails a test, never hangs it.
    - A fake sandbox Warden stops when its Cell is released, like the real one's connection.

See Also:
    - hivemind.queen.ticks.tools for the handlers under test.
    - builders.honey_wire for the raw link end, builders.basket for the deposit door.
"""

from __future__ import annotations

import asyncio
import dataclasses
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TypedDict, Unpack

import pytest
from builders.basket import BasketKit, make_kit
from builders.cells import make_cell
from builders.honey import open_test_honey_store
from builders.honey_wire import WireEnd, make_honey_access, make_honey_link
from builders.queen import make_queen_deps
from builders.royal_jelly import (
    make_failing_report,
    make_passing_report,
    make_scaffold_result,
    make_tool_request,
)

from hivemind.basket import (
    RefusalCode,
    Refuse,
    ResidentBasket,
    TransferReceiver,
    ack_to_wire,
    chunk_from_wire,
    offer_from_wire,
    refuse_to_wire,
)
from hivemind.basket.residency import ResidencyDecision
from hivemind.cell import Cell, CellKind, HoneyClearance, OsFamily, hive_stand_cell_id
from hivemind.forage import ModelSlot
from hivemind.hive import CellProvisionError, VirtualCellSpec
from hivemind.honey_store import SqliteHoneyStore
from hivemind.llm import FakeLLMProvider
from hivemind.manifest import BasketSection, RoyalJellySection, SandboxKind
from hivemind.queen.deps import QueenDeps, WardenLink
from hivemind.queen.inbox import to_inbox_item
from hivemind.queen.ticks.tools import handle_tool_item
from hivemind.royal_jelly.access import RoyalJellyAccess, limits_from_section
from hivemind.royal_jelly.registry import InMemoryCombRegistry
from hivemind.royal_jelly.scaffold import FakeScaffolder, ScaffoldResult, ToolRequestBrief
from hivemind.royal_jelly.spec import ToolSpec
from hivemind.supervision.attendant import InboxItem
from waggle.clock import FakeClock
from waggle.envelope import Envelope
from waggle.ids import CellId, MessageId, WardenId
from waggle.messages.base import WaggleMessage
from waggle.messages.basket import BasketChunk, BasketOffer
from waggle.messages.tool import ToolQuarantine, ToolQuarantineReport, ToolRequest
from waggle.signing import Ed25519Signer

__all__ = [
    "FakeSandboxProvider",
    "FakeWarden",
    "ToolRig",
    "WardenScript",
    "make_tool_rig",
]

_WAIT_S = 20.0  # A real-time guard so a missing message fails a test instead of hanging it.
_IDLE_S = 3600.0  # How long a fake Warden's read waits: it is cancelled long before.


class _AnyRoom:
    """A residency planner that always has room, for a fake Warden's tiny resident set."""

    async def plan(self, size_bytes: int) -> ResidencyDecision:
        """Admit a blob of any size."""
        del size_bytes
        return ResidencyDecision(True, (), "room.")


@dataclass(frozen=True, slots=True)
class WardenScript:
    """What a fake Warden does: refuse the blob, stay silent, or report a verdict."""

    verdict: str = "pass"  # "pass", or the name of the gate the canned report fails.
    refuse_offer: bool = False  # Answer basket.offer with basket.refuse.
    silent: bool = False  # Never answer tool.quarantine (the Queen's wait must time out).
    answers: bool = True  # False: hand every envelope to `inbox` and answer nothing.


class FakeWarden:
    """The Warden at the far end of one link: keeps blobs, answers orders, queues the rest."""

    def __init__(
        self,
        queen: tuple[QueenDeps, dict[WardenId, WardenLink]],
        cell: tuple[WardenLink, WireEnd],
        spool: Path,
        script: WardenScript | None = None,
        ordered: asyncio.Event | None = None,
    ) -> None:
        """Start serving the Warden's end of `cell`'s link; `ordered` is set on a Comb order."""
        self.deps, self.wardens = queen
        self.link, self.end = cell
        self._spool = spool
        self._script = script or WardenScript()
        access = self.deps.royal_jelly
        self._verifier = access.verifier if access is not None else None
        self.seen: list[str] = []  # Every kind the Queen sent it, in order.
        self.orders: list[ToolQuarantine] = []
        self._ordered = ordered
        self.blobs: dict[str, bytes] = {}  # Every blob received whole, by hash.
        self.inbox: asyncio.Queue[Envelope] = asyncio.Queue()
        self._receivers: dict[str, TransferReceiver] = {}
        self.task = asyncio.ensure_future(self._serve())

    async def next(self) -> Envelope:
        """Return the next envelope this Warden did not answer itself."""
        return await asyncio.wait_for(self.inbox.get(), _WAIT_S)

    async def deliver(
        self, message: WaggleMessage, correlation_id: MessageId | None = None
    ) -> Envelope:
        """Send `message` from this Warden into the Queen's handlers, as her tick would."""
        envelope = self.end.envelope_for(message, correlation_id=correlation_id)
        item = to_inbox_item(envelope, self.link.warden_id)
        assert await handle_tool_item(self.deps, self.wardens, item)
        return envelope

    async def _serve(self) -> None:
        """Read what the Queen sends and answer it like the real Warden, until cancelled."""
        while True:
            envelope = await self.end.next(_IDLE_S)
            self.seen.append(envelope.kind)
            payload = envelope.payload
            if not self._script.answers:
                await self.inbox.put(envelope)
            elif isinstance(payload, BasketOffer):
                await self._on_offer(payload, envelope)
            elif isinstance(payload, BasketChunk):
                await self._on_chunk(payload, envelope)
            elif isinstance(payload, ToolQuarantine):
                self.orders.append(payload)
                if self._ordered is not None:
                    self._ordered.set()
                if not self._script.silent:
                    await self.deliver(self._report(payload), MessageId(envelope.id))
            else:
                await self.inbox.put(envelope)

    async def _on_offer(self, payload: BasketOffer, envelope: Envelope) -> None:
        """Take (or refuse) an offer with the real receiver, verifying the Hive's signature."""
        assert self._verifier is not None
        offer = offer_from_wire(payload)
        receiver = self._receivers.setdefault(offer.sha256, TransferReceiver(self._spool))
        result = await receiver.on_offer(
            offer, self._verifier, HoneyClearance.C2, _AnyRoom(), OsFamily.LINUX
        )
        if self._script.refuse_offer and not isinstance(result, Refuse):
            await self.deliver(
                refuse_to_wire(
                    Refuse(
                        sha256=offer.sha256, code=RefusalCode.CLEARANCE, reason="a fake refusal."
                    )
                ),
                MessageId(envelope.id),
            )
            return
        wire = refuse_to_wire(result) if isinstance(result, Refuse) else ack_to_wire(result)
        await self.deliver(wire, MessageId(envelope.id))

    async def _on_chunk(self, payload: BasketChunk, envelope: Envelope) -> None:
        """Append a chunk; keep the blob once the receiver says it verified."""
        result = await self._receivers[payload.sha256].on_chunk(chunk_from_wire(payload))
        if not isinstance(result, Refuse) and result.complete:
            path = self._receivers[payload.sha256].completed_path
            assert path is not None
            self.blobs[payload.sha256] = path.read_bytes()
        wire = refuse_to_wire(result) if isinstance(result, Refuse) else ack_to_wire(result)
        await self.deliver(wire, MessageId(envelope.id))

    def _report(self, order: ToolQuarantine) -> ToolQuarantineReport:
        """Build the canned report a sandbox Warden would send for `order`."""
        spec = ToolSpec.from_json(order.spec_json)
        digest, size = order.package_sha256, order.package_size_bytes
        extra = {"path": order.path, "sandbox_cell_id": self.link.cell.id}
        gate = None if self._script.verdict == "pass" else self._script.verdict
        if gate is None:
            report = make_passing_report(spec, digest, size, **extra)
        else:
            report = make_failing_report(spec, digest, size, failing_gate=gate, **extra)
        return ToolQuarantineReport(
            tool_id=spec.tool_id,
            version=spec.version,
            passed=report.passed,
            path=order.path,
            sandbox_cell_id=self.link.cell.id,
            failing_gate=gate,
            report_json=report.to_json(),
            reason="canned",
        )


class FakeSandboxProvider:
    """A `SandboxCellProvider` that hands out a fake sandbox Cell, or fails, or waits on a gate."""

    def __init__(self, script: WardenScript | None = None) -> None:
        """Build the provider; `bind` gives it the Queen once her deps exist."""
        self.script = script or WardenScript()
        self.specs: list[VirtualCellSpec] = []
        self.released: list[CellId] = []
        self.sandbox_wardens: list[FakeWarden] = []
        # Per acquire: whether the Cell was already marked a sandbox when its Warden attached.
        self.marked_before_attach: list[bool] = []
        self.fail: str | None = None  # A reason: acquire raises CellProvisionError with it.
        self.gate: asyncio.Event | None = None  # When set, acquire waits for it.
        self.started = asyncio.Event()  # Set when an acquire begins.
        self.ordered = asyncio.Event()  # Set when a sandbox Warden receives the Comb order.
        self._queen: tuple[QueenDeps, dict[WardenId, WardenLink]] | None = None
        self._spool: Path | None = None

    def bind(self, queen: tuple[QueenDeps, dict[WardenId, WardenLink]], spool: Path) -> None:
        """Give the provider the Queen's deps and live Warden table, and a spool directory."""
        self._queen, self._spool = queen, spool

    async def acquire_sandbox(
        self, spec: VirtualCellSpec, on_provisioned: Callable[[CellId], None] | None = None
    ) -> WardenLink:
        """Record `spec`, attach a fake sandbox Cell's link and start its Warden."""
        assert self._queen is not None and self._spool is not None
        self.specs.append(spec)
        self.started.set()
        if self.gate is not None:
            await self.gate.wait()
        if self.fail is not None:
            raise CellProvisionError("fake", spec.image, self.fail)
        deps, wardens = self._queen
        cell = make_cell(kind=CellKind.VIRTUAL, clock=deps.clock)
        if on_provisioned is not None:
            on_provisioned(cell.id)  # As the real provider does: before the Warden attaches.
        self.marked_before_attach.append(cell.id in deps.tool_lane.sandbox_cells)
        link, end = make_honey_link(cell, deps.clock, deps.identity.hive_id, deps.identity.node_id)
        wardens[link.warden_id] = link
        warden = FakeWarden(
            self._queen, (link, end), self._spool / str(cell.id), self.script, self.ordered
        )
        self.sandbox_wardens.append(warden)
        return link

    async def release_sandbox(self, cell_id: CellId) -> None:
        """Record the release and stop that Cell's fake Warden."""
        self.released.append(cell_id)
        assert self._queen is not None
        wardens = self._queen[1]
        for warden in self.sandbox_wardens:
            if warden.link.cell.id == cell_id:
                warden.task.cancel()
                wardens.pop(warden.link.warden_id, None)


@dataclass(slots=True)
class ToolRig:
    """One Queen with a Lab, a Basket and one attached Warden, ready for a handler."""

    deps: QueenDeps
    peer: FakeWarden
    access: RoyalJellyAccess
    kit: BasketKit
    registry: InMemoryCombRegistry
    scaffolder: FakeScaffolder
    wardens: dict[WardenId, WardenLink] = field(default_factory=dict)
    honey_store: SqliteHoneyStore | None = None
    spool: Path = Path()
    extra: list[FakeWarden] = field(default_factory=list)

    @property
    def link(self) -> WardenLink:
        """The requesting Warden's link, as the Queen holds it."""
        return self.peer.link

    def request(self, **overrides: object) -> ToolRequest:
        """Build a hive-scope `tool.request` from the rig's Warden for the echo tool."""
        fields: dict[str, object] = {
            "warden_id": self.link.warden_id,
            "cell_id": self.link.cell.id,
            "required_capabilities": (),
        }
        fields.update(overrides)
        return make_tool_request(self.deps.clock, **fields)

    def item(self, payload: WaggleMessage, correlation_id: MessageId | None = None) -> InboxItem:
        """Wrap `payload` as the rig's Warden sent it, as the Queen's inbox would hold it."""
        envelope = self.peer.end.envelope_for(payload, correlation_id=correlation_id)
        return to_inbox_item(envelope, self.link.warden_id)

    async def send(
        self, payload: WaggleMessage, correlation_id: MessageId | None = None
    ) -> InboxItem:
        """Deliver `payload` to the Queen's tool handlers and return the item that carried it."""
        item = self.item(payload, correlation_id)
        assert await handle_tool_item(self.deps, self.wardens, item)
        return item

    async def reply(self) -> Envelope:
        """Read the next envelope the Queen sent the rig's Warden that it did not answer itself."""
        return await self.peer.next()

    async def settle(self) -> None:
        """Await every job the tool lane holds, as the Queen's later ticks would see them end."""
        lane = self.deps.tool_lane
        for _ in range(10):
            jobs = [*lane.jobs.values(), *lane.background]
            if not jobs:
                return
            await asyncio.wait_for(asyncio.gather(*jobs), timeout=60)

    def add_peer(self, cell: Cell, script: WardenScript | None = None) -> FakeWarden:
        """Attach another Warden on `cell` to the rig's Queen, served by a fake like the first."""
        deps = self.deps
        link, end = make_honey_link(cell, deps.clock, deps.identity.hive_id, deps.identity.node_id)
        self.wardens[link.warden_id] = link
        peer = FakeWarden((deps, self.wardens), (link, end), self.spool / str(cell.id), script)
        self.extra.append(peer)
        return peer

    async def aclose(self) -> None:
        """Stop the fake Wardens; call at the end of a test that started jobs."""
        self.peer.task.cancel()
        for other in self.extra:
            other.task.cancel()
        provider = self.deps.sandboxes
        if isinstance(provider, FakeSandboxProvider):
            for warden in provider.sandbox_wardens:
                warden.task.cancel()


def _hive_signer(kit: BasketKit) -> Ed25519Signer:
    """Return the kit's Hive key as the Ed25519 signer `RoyalJellyAccess` is typed to hold."""
    assert isinstance(kit.wiring.signer, Ed25519Signer)
    return kit.wiring.signer


def _echo_tool(brief: ToolRequestBrief) -> ScaffoldResult:
    """Scaffold the echo tool declaring no capability: it acts through no session."""
    return make_scaffold_result(brief, required_capabilities=())


class RigOptions(TypedDict, total=False):
    """The knobs a test may turn on `make_tool_rig`; every one is optional."""

    scaffolder: Callable[[ToolRequestBrief], ScaffoldResult]  # Default: the echo tool.
    sandbox: SandboxKind  # SUBPROCESS (the real Comb in a child process) or VIRTUAL (the fake).
    provider: FakeSandboxProvider  # The fake sandbox provider for VIRTUAL; a fresh one if omitted.
    cell: Cell  # The requesting Warden's Cell; a Linux Real Cell by default.
    script: WardenScript  # How the requesting Warden behaves; it acks every blob by default.
    lab: bool  # False builds a Queen with no Royal Jelly Lab at all.
    bound: bool  # False leaves the SCAFFOLDER slot unbound, so the Lab cannot scaffold.
    honey: bool  # True wires a real Honey Store on a temp file.
    llm: FakeLLMProvider  # The provider behind the Queen's own slots (her awake review).
    request_timeout_s: float  # `[royal_jelly] request_timeout_s`: every wait on a Warden's reply.
    max_retries: int  # `[royal_jelly] max_retries`: extra scaffolds after a failing report.


def _lab(
    tmp_path: Path, deps: QueenDeps, kit: BasketKit, options: RigOptions
) -> tuple[RoyalJellyAccess, InMemoryCombRegistry]:
    """Build the Lab's handles over the kit's Basket and a fresh in-memory registry."""
    registry = InMemoryCombRegistry(deps.clock)
    section = RoyalJellySection(
        sandbox=options.get("sandbox", SandboxKind.SUBPROCESS),
        request_timeout_s=options.get("request_timeout_s", 20.0),
        max_retries=options.get("max_retries", 1),
    )
    stand = hive_stand_cell_id(deps.identity.node_id)
    access = RoyalJellyAccess(
        registry=registry,
        basket=kit.store,
        deposits=kit.deposits,
        resident=ResidentBasket(kit.store, stand, 1 << 30, deps.clock),
        signer=_hive_signer(kit),
        signer_id=kit.signer_id,
        verifier=kit.verifier,
        config=section,
        basket_config=BasketSection(root=tmp_path / "basket"),
        limits=limits_from_section(section.limits),
        sandbox_kind=section.sandbox,
        scaffolder_bound=deps.bound_for(ModelSlot.QUEEN) if options.get("bound", True) else None,
    )
    return access, registry


async def make_tool_rig(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, **options: Unpack[RigOptions]
) -> ToolRig:
    """Build a Queen over a real Basket and Comb Registry, with one Warden's link attached.

    Args:
        tmp_path: A writable directory for the Basket and the spools.
        monkeypatch: Swaps the model-backed scaffolder for the fake one.
        **options: See `RigOptions`.

    Returns:
        The rig. A provider is bound to the Queen's deps and live Warden table.
    """
    clock = FakeClock()
    kit = await make_kit(tmp_path / "kit")
    deps, first, _end = make_queen_deps(
        clock, cell=options.get("cell"), fake_provider=options.get("llm")
    )
    link, end = make_honey_link(first.cell, clock, deps.identity.hive_id, deps.identity.node_id)
    access, registry = _lab(tmp_path, deps, kit, options)
    fake = FakeScaffolder(options.get("scaffolder", _echo_tool))
    monkeypatch.setattr("hivemind.queen.ticks.tools.requests.ModelScaffolder", lambda *_: fake)
    virtual = options.get("sandbox") is SandboxKind.VIRTUAL
    sandboxes = options.get("provider", FakeSandboxProvider()) if virtual else None
    (tmp_path / "honey").mkdir()
    store = await open_test_honey_store(tmp_path / "honey", clock) if options.get("honey") else None
    deps = dataclasses.replace(
        deps,
        royal_jelly=access if options.get("lab", True) else None,
        sandboxes=sandboxes,
        honey=make_honey_access(store, clock) if store is not None else None,
    )
    wardens = {link.warden_id: link}
    peer = FakeWarden((deps, wardens), (link, end), tmp_path / "spool", options.get("script"))
    if sandboxes is not None:
        sandboxes.bind((deps, wardens), tmp_path / "sandbox-spool")
    return ToolRig(deps, peer, access, kit, registry, fake, wardens, store, tmp_path / "spool")
