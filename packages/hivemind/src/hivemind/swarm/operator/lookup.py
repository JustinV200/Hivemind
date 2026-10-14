"""Resolve the operator's words for a device to the one enrolled device they name.

An operator rarely types a `device_<ULID>`: they say the label they gave at `hive swarm invite`
("kitchen-pi"), or paste a device id or a Cell id (the Real Cell, a unit of compute the Hive
borrows, that the device became) from `hive swarm list`. `find_node` is the one rule every
`hive swarm` command uses to turn that text into a `SwarmNode`, so they all agree on what a name
means. An id names its device exactly. A label names the device that carries it, with one
refinement that follows from how the Swarm works: a revoked device keeps its row, and its label,
for the audit, so an operator who revokes "kitchen-pi" and enrols the machine again under the
same label means the new device from then on. A label therefore prefers the devices that are not
revoked, and falls back to a revoked one only when it is the sole match, so `revoke` on an
already revoked device can still say so.

Fits into the Hive:
    Layer 3 (sources of Cells, and capabilities handed down), inside `hivemind.swarm.operator`.
    Called by `OperatorDesk.find`, which the `hive swarm` commands use. Pure: calls into
    hivemind.swarm.models, .node_state and .operator.errors only.

Key invariants:
    - An id match is exact and wins over any label: a device whose label is another device's id
      cannot hijack that id.
    - Never a guess: no match is `UnknownNodeError`, and several equally good matches are
      `AmbiguousNodeError` naming every device id so the operator can pick one.
    - Pure: nothing is read or written; the caller hands in the registry's nodes.

See Also:
    - hivemind.swarm.operator.desk for the acts these names are used for.
    - docs/waggle/enrolment.md section 3.1 for the label an invite carries.
"""

from __future__ import annotations

from collections.abc import Sequence

from hivemind.swarm.models import SwarmNode
from hivemind.swarm.node_state import NodeStatus
from hivemind.swarm.operator.errors import AmbiguousNodeError, UnknownNodeError

__all__ = ["find_node"]


def find_node(nodes: Sequence[SwarmNode], reference: str) -> SwarmNode:
    """Return the one enrolled device `reference` names.

    Args:
        nodes: Every enrolled device, revoked ones included (`SwarmRegistry.nodes()`).
        reference: A device id, a Cell id or a label, as the operator typed it; surrounding
            whitespace is ignored.

    Returns:
        The matching device.

    Raises:
        UnknownNodeError: Nothing matches, or the reference is blank.
        AmbiguousNodeError: A label matches several devices equally well.
    """
    wanted = reference.strip()
    # An id is unique by construction, so it settles the question before any label is read.
    for node in nodes:
        if wanted in (node.device_id, node.cell_id):
            return node
    carriers = [node for node in nodes if wanted and node.label == wanted]
    # A revoked device keeps its label for the audit, so a live device with the label comes first.
    live = [node for node in carriers if node.status is not NodeStatus.REVOKED]
    chosen = live or carriers
    if not chosen:
        raise UnknownNodeError(wanted)
    if len(chosen) > 1:
        raise AmbiguousNodeError(wanted, [node.device_id for node in chosen])
    return chosen[0]
