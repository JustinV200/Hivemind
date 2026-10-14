"""Define GateTerms and GateContext: the numbers the swarm gate works to, and its Queen.

The swarm gate is built before the Queen exists (the composition root assembles the Hive's parts,
then runs it), and works with her only once it is started. So what it needs from the manifest is
gathered here, once, as plain data: how often it reconciles, how long it holds a lost device's
tasks before deciding (`[swarm] unreachable_grace_s`), how long a Nuc may stay out of reach before
its tasks are re-placed too (`[supervision] max_offline_s`), the runtime version the Hive installs
on a device it colonizes, and the thresholds `decide_colonize` weighs (`[swarm.ladder]`). The
`GateContext` is the other half: the Queen and her collaborators, bound when the gate starts, so
the pieces that act on tasks (the hold, the colonization) read them from one place and a test can
bind a Queen without opening a listener.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside `hivemind.queen.swarm_gate`.
    Built by the composition root (`hivemind.cli.compose.swarm`) and bound by `SwarmGate.start`;
    read by every module of this package. Calls into `hivemind.queen` (the Queen's type) only.

Key invariants:
    - `GateTerms` holds numbers and one version string, nothing live: it is frozen and read by
      every part without a lock.
    - A `GateContext` is created once per gate start and is never partly filled.

See Also:
    - hivemind.manifest.schema.swarm for the `[swarm]` knobs these carry.
    - hivemind.cli.compose.swarm.build for where the terms are filled from the manifest.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

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
    """

    interval_s: float
    unreachable_grace_s: float
    max_offline_s: float
    runtime_version: str
    relief_pressure: float
    min_free_memory_bytes: int


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
