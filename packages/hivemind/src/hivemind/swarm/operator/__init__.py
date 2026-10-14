"""Re-export the operator's desk: the human's acts on devices that are already enrolled.

An enrolled device (an existing machine the Hive borrows as a Real Cell, a unit of compute) is
named by the operator with a device id, a Cell id or a label, and then revoked, given another
access level or Comb Shield tier (a security tier), given directories for its Warden to watch,
or asked to move one rung up or down the runtime ladder. `lookup` is the one rule for what a name
means, `desk` makes each act and records its trail event in the same transaction, `rung` writes the
wish and the ceiling that a promote or a demote stands for (a demote pins the device where it
lands, so the Queen does not climb back), `roots` judges the directories named for watching, and
`errors` holds the ways a name or an act is refused.
This file is the package's face: a caller writes `from hivemind.swarm.operator import
OperatorDesk` without knowing the split (codingrules 5.2, 5.4).

Fits into the Hive:
    Layer 3 (sources of Cells, and capabilities handed down), inside `hivemind.swarm`. Used by the
    `hive swarm` commands (Layer 7). Calls into the rest of hivemind.swarm only.

Key invariants:
    - This file holds re-exports and __all__ only; the modules behind it define every name.
    - The desk writes the registry and the trail and never talks to a device; the running Hive's
      swarm gate does the work on its next beat.

See Also:
    - docs/waggle/enrolment.md sections 10 and 11 for a change and a revocation.
    - hivemind.queen.swarm_gate.reconcile for how the running Hive applies them.

Public API:
    - OperatorDesk, NodeChange: the acts (revoke, set_access, set_shield, set_watch_roots, move)
      and their result (desk).
    - find_node: the rule that turns an id or a label into the one device it names (lookup).
    - ask_for_rung: the wish and the ceiling a promote or a demote writes (rung).
    - checked_roots: the directories named for watching, judged and put in normal form (roots).
    - NodeReferenceError, UnknownNodeError, AmbiguousNodeError, RungUnavailableError,
      TierRefusedError, WatchRootRefusedError: the ways a name or an act is refused (errors).
"""

from hivemind.swarm.operator.desk import NodeChange, OperatorDesk
from hivemind.swarm.operator.errors import (
    AmbiguousNodeError,
    NodeReferenceError,
    RungUnavailableError,
    TierRefusedError,
    UnknownNodeError,
    WatchRootRefusedError,
)
from hivemind.swarm.operator.lookup import find_node
from hivemind.swarm.operator.roots import checked_roots
from hivemind.swarm.operator.rung import ask_for_rung

__all__ = [
    "AmbiguousNodeError",
    "NodeChange",
    "NodeReferenceError",
    "OperatorDesk",
    "RungUnavailableError",
    "TierRefusedError",
    "UnknownNodeError",
    "WatchRootRefusedError",
    "ask_for_rung",
    "checked_roots",
    "find_node",
]
