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

Roadmap step 10.3a: the Cell announces the tier it was provisioned at (`HIVEMIND_COMB_SHIELD`, no
longer always MEADOW), and `CellReady` carries check results exactly when that tier attests. A
Night Veil Cell reports the two checks it can make of its own control link
(`control_link_attestation`): that Waggle dials the Queen only through the SOCKS proxy, and that
the Queen it reaches is her onion service, which the `CellReady` arriving over that link proves.
They mirror two of `hivemind.hive.night_veil.results.CHECK_NAMES`; the Queen's own attestation of
the image (`hivemind.queen.cell_gate.provider`) stays the one that decides readiness.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside `hivemind.cli.in_cell`. Calls into
    `hivemind.cell` (Cell), `hivemind.forage` (ForageCapacity) and waggle (envelope, ids, messages,
    transport, uris) only. Built and run by `hivemind.cli.in_cell.main.run_in_cell_warden`.

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
from urllib.parse import urlsplit

from hivemind.cell.models import Cell
from hivemind.cell.tiers import CombShieldLevel
from hivemind.forage import ForageCapacity
from waggle.clock import Clock
from waggle.envelope import Hop, wrap
from waggle.ids import HiveId, NodeId, WardenId
from waggle.messages.cell.status import AttestationCheck, CellHeartbeat, CellMode, CellReady
from waggle.messages.forage import CapacityReport, CapacityTrigger, LocalPoolUsage
from waggle.transport.base import Transport
from waggle.transport.socks import SocksProxy
from waggle.uris import is_onion_service_host

# The two checks a Night Veil Cell makes of its own control link; the names mirror
# hivemind.hive.night_veil.results.CHECK_NAMES, where the Queen's attestation runs the same two.
VIA_SOCKS_CHECK = "waggle_socket_via_socks"
HIDDEN_SERVICE_CHECK = "hidden_service_reachable"

__all__ = [
    "HIDDEN_SERVICE_CHECK",
    "VIA_SOCKS_CHECK",
    "CellLinkDeps",
    "announce",
    "control_link_attestation",
    "send_capacity_report",
    "send_cell_heartbeat",
]

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
        attestation: The checks `CellReady` carries for this Cell's tier: none for MEADOW, the
            control-link checks for NIGHT_VEIL (`control_link_attestation`).
    """

    transport: Transport
    cell: Cell
    warden_id: WardenId
    hive_id: HiveId
    node_id: NodeId
    clock: Clock
    heartbeat_interval_s: float
    runtime_version: str
    attestation: tuple[AttestationCheck, ...] = ()


def control_link_attestation(
    queen_waggle_url: str, proxy: SocksProxy | None
) -> tuple[AttestationCheck, ...]:
    """Return the two checks a Night Veil Cell makes of its own control link.

    Args:
        queen_waggle_url: The URL this Cell dials the Queen at.
        proxy: The SOCKS proxy its transport dials through, or None for a direct dial.

    Returns:
        `waggle_socket_via_socks` (every dial goes through the proxy) and
        `hidden_service_reachable` (the Queen answers at her onion service, which the `CellReady`
        carrying this result proves by arriving), each passed or failed as the facts are.
    """
    host = urlsplit(queen_waggle_url).hostname or ""
    via_socks = AttestationCheck(
        name=VIA_SOCKS_CHECK,
        has_passed=proxy is not None,
        detail=(
            f"Waggle dials the Queen only through the SOCKS proxy {proxy}; never directly."
            if proxy is not None
            else "No SOCKS proxy is set: Waggle would dial the Queen directly."
        ),
    )
    onion = is_onion_service_host(host)
    hidden_service = AttestationCheck(
        name=HIDDEN_SERVICE_CHECK,
        has_passed=onion,
        detail=(
            f"This CellReady reaches the Queen at her onion service {host}, over Tor."
            if onion
            else f"The Queen's URL names {host!r}, which is not an onion service."
        ),
    )
    return (via_socks, hidden_service)


async def announce(deps: CellLinkDeps) -> None:
    """Connect `deps.transport` and send this Cell's one signed CellReady.

    Args:
        deps: This module's own collaborators.
    """
    await deps.transport.connect()
    await deps.transport.send(wrap(_build_cell_ready(deps), _hop(deps), clock=deps.clock))


async def send_capacity_report(deps: CellLinkDeps, capacity: ForageCapacity) -> None:
    """Send this Cell's ForageCapacity, since CellReady carries none.

    Args:
        deps: This module's own collaborators.
        capacity: This Cell's own capacity, from `InCellSpawnConfig.capacity`: the reservation
            its bootstrap names, the figures the Queen placed it by (`hivemind.cli.in_cell.config`).
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
        # a tiered Cell's shield is verified when every check its CellReady carried passed.
        is_shield_verified=deps.cell.comb_shield is CombShieldLevel.MEADOW
        or (bool(deps.attestation) and all(check.has_passed for check in deps.attestation)),
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
        # Empty exactly when comb_shield is MEADOW (CellReady's own validator); a Night Veil
        # Cell carries its control-link checks (roadmap step 10.3a, main.py builds them).
        attestation=deps.attestation,
        runtime_version=deps.runtime_version,
    )
