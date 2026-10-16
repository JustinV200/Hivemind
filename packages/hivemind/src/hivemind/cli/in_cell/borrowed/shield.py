"""Hold a colonized device's Warden to its Propolis tier: the report it makes, the lease it gates.

A device enrolled in the Propolis tier (the Comb Shield tier that needs a VPN and no Tor) keeps
that tier when its Warden moves onto it (ADR-0052: the Warden moves first, the node's tier rides on
the packet's environment). On the Hive Stand the Level 0 Warden's source is wrapped in
`ShieldedSource`, so a lease on the device is returned only once `PropolisChecks` has run the
fixed read-only tunnel commands through the device's session and recorded the outcome; this module
does the same for the Warden on the device, over the `HiveStandSource` it leases its own machine
through, so "the device's Warden verifies the device's own tunnel before each lease" (roadmap
11.8a) holds at every rung and the same checks, judges and trail events serve both. It also makes
the report a tiered Cell's `CellReady` must carry (the wire refuses one with no checks above
MEADOW): the same tunnel checks, run through a lease-free `ObserverSession` on the machine, with
the commands the device's operating system has (a Linux device reads its route tables, a macOS one
runs `netstat -rn`), because unlike a Virtual Cell image a device is not always Linux. The report
is what the Cell says of itself and never a gate: a device with no tunnel still announces, its
checks failed and its heartbeat saying the shield is not verified, and the lease-time verification
is what keeps work off it. A MEADOW device promises nothing about its network, reports no checks
and has its source left as it is.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside `hivemind.cli.in_cell.borrowed`. Called by
    `hivemind.cli.in_cell.borrowed.run` (the shielded source) and `.deps` (the announcer's
    report). Calls into `hivemind.cell` (Cell, the tiers, RealCellSource), `hivemind.cell.local`
    (ObserverSession), `hivemind.cell.tunnel` (check_tunnel), `hivemind.swarm` (PropolisChecks,
    ShieldedSource, SwarmRecorder) and waggle only.

Key invariants:
    - Nothing here installs, starts or reroutes anything: the commands are `hivemind.cell.tunnel`'s
      fixed read-only ones, run through a session that can run a command and nothing else.
    - The report never raises: a command that cannot run is a failed check, and a system the Hive
      has no commands for reports one failed `platform_supported` check, so the CellReady is always
      valid and always honest.
    - A check's detail is the judges' short stable reason, never a route, an address or output.
    - The source is wrapped exactly when the tier is PROPOLIS; the wrapper is the very one the
      Hive Stand uses, so a refused lease is `ShieldUnverifiedError` (a `LeaseRefusedError`, which
      the Warden's lease tick turns into WATCH, holding the task and failing nothing).

See Also:
    - docs/adr/0057-propolis-is-verified-by-looking.md for the checks and why one set serves all.
    - hivemind.cli.compose.swarm.device_warden for the Level 0 Warden's `_shielded`, the twin.
    - hivemind.cli.in_cell.propolis for a Virtual Cell's own report, which reads files instead.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from hivemind.cell import Cell, CombShieldLevel, RealCellSource
from hivemind.cell.local import ObserverSession
from hivemind.cell.tunnel import check_tunnel
from hivemind.cli.in_cell.borrowed.config import BorrowedConfig
from hivemind.swarm import PropolisChecks, ShieldedSource, SwarmRecorder
from waggle.clock import Clock
from waggle.messages.cell.status import AttestationCheck

# How a Cell's own report for its CellReady is made: the checks for the Cell as the source
# describes it now; empty for a tier that attests nothing.
Attester = Callable[[Cell], Awaitable[tuple[AttestationCheck, ...]]]

__all__ = ["Attester", "own_shield_report", "shielded_source"]


def own_shield_report(config: BorrowedConfig, clock: Clock) -> Attester:
    """Return the reporter of the checks this device's CellReady carries, by its tier.

    Args:
        config: The device Warden's configuration; its state directory is where the observer
            session's commands start (it is never written to).
        clock: Times every command the session runs.

    Returns:
        An `Attester`: for a PROPOLIS Cell, the tunnel checks run on this machine with the fixed
        commands of its operating system; for any other tier, no checks.
    """

    async def attest(cell: Cell) -> tuple[AttestationCheck, ...]:
        """Judge this machine's own tunnel, through a session that holds no lease."""
        if cell.comb_shield is not CombShieldLevel.PROPOLIS:
            return ()  # MEADOW attests nothing, and no other tier reaches a device.
        session = ObserverSession(config.state_dir, clock)
        try:
            # Never raises: a command that fails or is unsupported is a failed check.
            report = await check_tunnel(session, cell.capabilities.os)
        finally:
            await session.close()
        return tuple(
            AttestationCheck(name=check.name, has_passed=check.has_passed, detail=check.reason)
            for check in report.checks
        )

    return attest


def shielded_source(
    config: BorrowedConfig, source: RealCellSource, recorder: SwarmRecorder
) -> RealCellSource:
    """Wrap `source` so a PROPOLIS lease is returned only once the tunnel is verified.

    Args:
        config: The device Warden's configuration; its Cell's tier decides.
        source: The source the Warden leases its own machine through.
        recorder: The trail, clock and identity `swarm.shield_verified`/`swarm.shield_refused`
            are recorded with: the Warden's own, so the record ships to the Queen with its segment.

    Returns:
        `source` itself for a MEADOW device, else a `ShieldedSource` over it.
    """
    if config.cell.comb_shield is not CombShieldLevel.PROPOLIS:
        return source  # Meadow has nothing to verify.
    return ShieldedSource(source, PropolisChecks(recorder))
