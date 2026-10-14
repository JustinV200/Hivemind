"""Define GateTerms and GateContext: the numbers the swarm gate works to, and its Queen.

The swarm gate is built before the Queen exists (the composition root assembles the Hive's parts,
then runs it), and works with her only once it is started. So what it needs from the manifest is
gathered here, once, as plain data: how often it reconciles, how long it holds a lost device's
tasks before deciding (`[swarm] unreachable_grace_s`), how long a Nuc may stay out of reach before
its tasks are re-placed too (`[supervision] max_offline_s`), the runtime version the Hive installs
on a device it colonizes, the thresholds `decide_colonize` weighs (`[swarm.ladder]`), and what
`decide_nuc` weighs: the seat pressure past which a Nuc is wanted (`[swarm.nuc]`) and the model
servers the Hive allows a device to start, each with the Forage map entries it may load
(`[llm.servers]`). Each rung also carries the two times that keep the Queen's own moves from
flapping (`lower_after_s` and `retry_after_s`; `hivemind.queen.swarm_gate.rungs` reads them). The
`GateContext` is the other half: the Queen and her collaborators, bound when
the gate starts, so the pieces that act on tasks (the hold, the colonization) read them from one
place and a test can bind a Queen without opening a listener.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside `hivemind.queen.swarm_gate`.
    Built by the composition root (`hivemind.cli.compose.swarm`) and bound by `SwarmGate.start`;
    read by every module of this package. Calls into `hivemind.manifest` (the Nuc pressure's
    default), `hivemind.queen` (the Queen's type) and `hivemind.swarm` (the server type) only.

Key invariants:
    - `GateTerms` holds numbers, one version string and the Hive's allowlist of servers, nothing
      live: it is frozen and read by every part without a lock.
    - A `GateContext` is created once per gate start and is never partly filled.

See Also:
    - hivemind.manifest.schema.swarm for the `[swarm]` knobs these carry.
    - hivemind.cli.compose.swarm.build for where the terms are filled from the manifest.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from hivemind.manifest.schema.swarm import (
    DEFAULT_LOWER_AFTER_S,
    DEFAULT_NUC_SEAT_PRESSURE,
    DEFAULT_RETRY_AFTER_S,
)
from hivemind.swarm import OfferedServer

if TYPE_CHECKING:
    from hivemind.queen.deps import QueenDeps
    from hivemind.queen.queen import Queen

__all__ = ["GateContext", "GateTerms"]


@dataclass(frozen=True, slots=True)
class GateTerms:
    """What the manifest says the swarm gate should do, and when.

    Attributes:
        interval_s: Seconds between reconcile passes (`[swarm] heartbeat_interval_s`).
        unreachable_grace_s: Seconds a lost device's tasks are held before the Queen decides
            (`[swarm] unreachable_grace_s`); 0 decides at once.
        max_offline_s: Seconds a Nuc's tasks are kept waiting for it once its grace has ended
            (`[supervision] max_offline_s`).
        runtime_version: The HiveMind runtime version a colonized device installs, the Hive's own.
        relief_pressure: Hive Stand pressure past which a device may be colonized to relieve it
            (`[swarm.ladder] relief_pressure`).
        min_free_memory_bytes: Free memory a device must keep beyond a Warden's and a sub-bee's
            footprints (`[swarm.ladder] min_free_memory_bytes`).
        nuc_seat_pressure: Hive Stand seat pressure past which a colonized device is worth a model
            server of its own (`[swarm.nuc] seat_pressure`).
        servers: The model servers the Hive allows a device to start, with the Forage map entries
            each may load, in manifest order (`[llm.servers]` and `[forage.map]`); a device's
            Warden can start only one of these that its own manifest also declares.
        lower_after_s: Seconds every reason the Queen colonized a device for must have been gone
            before she takes its Warden back (`[swarm.ladder] lower_after_s`).
        retry_after_s: Seconds she leaves a device alone after a colonization or withdrawal she
            began failed (`[swarm.ladder] retry_after_s`).
        nuc_lower_after_s: The same wait before she stops a model server she started
            (`[swarm.nuc] lower_after_s`).
        nuc_retry_after_s: The same pause after a Nuc promotion or demotion she began failed
            (`[swarm.nuc] retry_after_s`).
    """

    interval_s: float
    unreachable_grace_s: float
    max_offline_s: float
    runtime_version: str
    relief_pressure: float
    min_free_memory_bytes: int
    nuc_seat_pressure: float = DEFAULT_NUC_SEAT_PRESSURE
    servers: tuple[OfferedServer, ...] = ()
    lower_after_s: float = DEFAULT_LOWER_AFTER_S
    retry_after_s: float = DEFAULT_RETRY_AFTER_S
    nuc_lower_after_s: float = DEFAULT_LOWER_AFTER_S
    nuc_retry_after_s: float = DEFAULT_RETRY_AFTER_S


@dataclass(frozen=True, slots=True)
class GateContext:
    """The Queen the gate serves, and what she works with; bound when the gate starts.

    Attributes:
        queen: The Queen every device Warden is attached to.
        deps: Her collaborators: the Brood Chamber, the Forage ledger, the dispatcher's books, the
            trail and the Guard's enforcer.
    """

    queen: Queen
    deps: QueenDeps
