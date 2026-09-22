"""Announce this Cell to the Queen: connect out, send CellReady, CapacityReport, CellHeartbeat.

Roadmap step 5.5's own entry-point loop used to run entirely inside a standalone `CellLink`
(`waggle.loop.TickLoop`), because no real `hivemind.wardens.warden.Warden` was wired up to send
`CellReady`/heartbeat/handle `Shutdown` itself. This dispatch wires that real Warden in
(`hivemind.cli.in_cell.main`), whose own tick loop already sends its supervision `Heartbeat` and,
through `hivemind.wardens.autopilot.table`'s new `WardenAction.STOP` case, already stops cleanly on
`Shutdown`/`CellTeardownRequest` -- so the only thing this module still owns is the three frames
that must go out *before* a `Warden` exists to send anything at all: a signed `CellReady` (ADR-0027:
"the image's entry point starts a Warden, and the Warden dials out... sends a signed CellReady...
then heartbeats"), the `CapacityReport` `CellReady` itself carries no room for
(`waggle.messages.cell.status.CellReady`'s own shape has no `ForageCapacity` field), and one
`CellHeartbeat` so `hivemind.queen.cell_gate.listener.CellListener`'s own readiness gate -- which
waits for exactly `CellReady` then `CellHeartbeat`, unmodified by this dispatch -- resolves before
this process ever calls `Warden.start()`. `Warden` itself never sends `CellHeartbeat` again after
that: `hivemind.queen.cell_gate.listener`'s own known-gap note says today's `CellListener` never
reads anything past that first pair anyway (this dispatch's own report names the recurring-liveness
gap that leaves).

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside `hivemind.cli.in_cell`. Calls into
    `hivemind.cell` (Cell), `hivemind.forage` (ForageCapacity) and waggle (envelope, ids, messages,
    transport) only. Built and run by `hivemind.cli.in_cell.main.run_in_cell_warden`.

Key invariants:
    - `announce` connects the transport and sends the one `CellReady` this Cell ever sends; it
      must be called, and awaited, before anything else is sent on `deps.transport`.
    - `send_capacity_report` and `send_cell_heartbeat` may be called only after `announce`, and
      `send_cell_heartbeat` only after `send_capacity_report` -- `CellListener`'s own gate accepts
      `CellHeartbeat` at any point once `CellReady` has arrived, but sending the capacity report
      first means a Queen that starts reading it sooner never sees a Cell reported ready with no
      capacity behind it.

See Also:
    - docs/adr/0027-virtual-cells-connect-outbound-only-and-boot-a-warden.md for the connection
      direction and the "signed CellReady first" order this module implements.
    - waggle.messages.cell.status for CellReady/CellHeartbeat, the two messages
      hivemind.queen.cell_gate.listener.CellListener's readiness gate waits for.
    - waggle.messages.forage.hosting for CapacityReport, the message CellReady has no room for.
    - hivemind.cli.in_cell.config for CellLinkDeps, this module's own inputs.
    - hivemind.cli.in_cell.main for run_in_cell_warden, this module's one caller.
"""

from __future__ import annotations

from dataclasses import dataclass

from hivemind.cell.models import Cell
from hivemind.cell.tiers import CombShieldLevel
from hivemind.forage import ForageCapacity
from waggle.clock import Clock
from waggle.envelope import Hop, wrap
from waggle.ids import HiveId, NodeId, WardenId
from waggle.messages.cell.status import CellHeartbeat, CellMode, CellReady
from waggle.messages.forage import CapacityReport, CapacityTrigger, LocalPoolUsage
from waggle.transport.base import Transport

__all__ = ["CellLinkDeps", "announce", "send_capacity_report", "send_cell_heartbeat"]

# The Cell has just come up with no lease and no sub-bees yet, for both frames this module sends;
# Warden.start() (called right after this module's three sends) is what actually leases the Cell.
_NO_LOCAL_POOL_USAGE = LocalPoolUsage(
    sub_bees_active=0, model_vram_bytes=0, model_disk_bytes=0, seats_exported=0
)


@dataclass(frozen=True, slots=True)
class CellLinkDeps:
    """Everything this module's three announce functions are built with (codingrules section 5.1).

    Attributes:
        transport: This Cell's own dial-out connection to the Queen; unconnected until `announce`
            calls `transport.connect()`.
        cell: This Cell's own probed description (`hivemind.wardens.spawn.in_cell.
            InCellSpawnSource.cells()`), what every frame this module sends describes.
        warden_id: This Warden's own freshly minted id; the `sender` of every envelope this
            module sends.
        hive_id: The Queen's own bee address; the `recipient` of every envelope this module sends.
        node_id: This Cell's own freshly minted node id; stamped on every envelope's `node_id`
            (the identity the Codec's Signer signs as).
        clock: Injected time source for every mint and timestamp.
        heartbeat_interval_s: The cadence `send_cell_heartbeat` reports on
            `CellHeartbeat.interval_s`.
        runtime_version: The installed `hivemind` distribution version, carried on `CellReady`.
    """

    transport: Transport
    cell: Cell
    warden_id: WardenId
    hive_id: HiveId
    node_id: NodeId
    clock: Clock
    heartbeat_interval_s: float
    runtime_version: str


async def announce(deps: CellLinkDeps) -> None:
    """Connect `deps.transport` and send this Cell's one signed CellReady.

    Args:
        deps: This module's own collaborators.
    """
    await deps.transport.connect()
    await deps.transport.send(wrap(_build_cell_ready(deps), _hop(deps), clock=deps.clock))


async def send_capacity_report(deps: CellLinkDeps, capacity: ForageCapacity) -> None:
    """Send this Cell's probed ForageCapacity, since CellReady carries none.

    Args:
        deps: This module's own collaborators.
        capacity: This Cell's own probed capacity, from `InCellSpawnConfig.capacity`
            (`hivemind.cli.in_cell.config`).
    """
    report = CapacityReport(
        cell_id=deps.cell.id,
        trigger=CapacityTrigger.PROVISIONED,
        host=capacity.host.to_wire(),
        model_servers=(),  # v0: no model server runs on a base-ubuntu Cell (roadmap step 5.3).
        max_sub_bees=capacity.max_sub_bees,
        usage=_NO_LOCAL_POOL_USAGE,
    )
    await deps.transport.send(wrap(report, _hop(deps), clock=deps.clock))


async def send_cell_heartbeat(deps: CellLinkDeps) -> None:
    """Send one CellHeartbeat so the Queen's readiness gate resolves (module docstring)."""
    heartbeat = CellHeartbeat(
        cell_id=deps.cell.id,
        mode=CellMode.ACTIVE,
        lease_ids=(),
        worker_count=0,
        # A receiver rule (waggle.messages.cell.status's own docstring): always true for MEADOW;
        # this Cell has no tiered attestation to report otherwise (_build_cell_ready below).
        is_shield_verified=deps.cell.comb_shield is CombShieldLevel.MEADOW,
        interval_s=deps.heartbeat_interval_s,
    )
    await deps.transport.send(wrap(heartbeat, _hop(deps), clock=deps.clock))


def _hop(deps: CellLinkDeps) -> Hop:
    """This module's own addressing: this Warden to the Queen, from this Cell's own node."""
    return Hop(sender=deps.warden_id, recipient=deps.hive_id, node_id=deps.node_id)


def _build_cell_ready(deps: CellLinkDeps) -> CellReady:
    """Build this Cell's one CellReady from its probed capabilities and this module's own ids."""
    platform, capabilities = deps.cell.capabilities.to_wire()
    return CellReady(
        cell_id=deps.cell.id,
        warden_id=deps.warden_id,
        platform=platform,
        capabilities=capabilities,
        # .to_wire(): Cell.access_level/comb_shield are hivemind's own mirror enums
        # (hivemind.cell.tiers), never the waggle.messages.labels wire form an Envelope carries.
        access_level=deps.cell.access_level.to_wire(),
        comb_shield=deps.cell.comb_shield.to_wire(),
        # Empty exactly when comb_shield is MEADOW (CellReady's own validator); this dispatch's
        # Cell is always provisioned at MEADOW (hivemind.cli.in_cell.config), so there is nothing
        # to report here yet -- Night Veil/Propolis attestation is a later roadmap step (5.7b).
        attestation=(),
        runtime_version=deps.runtime_version,
    )
