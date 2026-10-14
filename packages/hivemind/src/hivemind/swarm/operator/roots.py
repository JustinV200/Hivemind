"""Check the directories the operator names for a device's Warden to watch.

A Warden (the always-on supervisor of one Cell) with no bees working watches its idle Cell, and
on a device whose Warden runs on the Hive Stand it asks the Pollen Packet for the process list,
resource use and, inside directories the operator names, the tail of log files and the files that
changed (ADR-0054). The operator names those directories with `hive swarm watch`, in the device's
own spelling, and `checked_roots` is the one rule for what a list of them may be before it is
stored: each is an absolute path on the device (`/var/log/app`, or a drive letter and a folder on
Windows), none is more than the wire carries, and each is one the device's granted capabilities
let the Hive read. The result is the
list in its normal form: separators and `.`/`..` segments settled, a directory named twice kept
once, and a directory that lies inside another one named dropped, because the packet reads a
directory's whole tree and would otherwise report every file under the inner one twice.

Fits into the Hive:
    Layer 3 (sources of Cells, and capabilities handed down), inside `hivemind.swarm.operator`.
    Called by `OperatorDesk.set_watch_roots`. Calls into `hivemind.swarm.enrolment` (the
    capabilities), `hivemind.swarm.session` (the device's path rules) and waggle's watch bounds.

Key invariants:
    - Pure: no file system is read, so a path means what the device would make of its text and not
      what the Hive Stand's own disk holds.
    - The result is at most the wire's number of roots, each at most the wire's length, and the
      packet will not refuse the request for any of them.
    - A refusal names the position of the directory, never its text.

See Also:
    - docs/adr/0054-access-levels-watch-mode-and-the-patrol.md for where the roots come from.
    - hivemind.swarm.watch for how the Warden's probe asks for them.
"""

from __future__ import annotations

from collections.abc import Sequence

from hivemind.swarm.enrolment import grants_read
from hivemind.swarm.models import SwarmNode
from hivemind.swarm.operator.errors import WatchRootRefusedError
from hivemind.swarm.session import DevicePathRule, absolute_device_path
from waggle.messages.swarm.watch import MAX_OBSERVE_ROOTS, MAX_OBSERVED_PATH_CHARS

__all__ = ["checked_roots"]


def checked_roots(node: SwarmNode, roots: Sequence[str]) -> tuple[str, ...]:
    """Check the directories named for `node`'s Warden to watch, and return them in normal form.

    Args:
        node: The device they are for; its granted capabilities say what the Hive may read.
        roots: What the operator typed, in the device's own spelling; empty to watch none.

    Returns:
        The directories to store: normalised, each once, none inside another.

    Raises:
        WatchRootRefusedError: There are more than the wire carries, or one is not an absolute
            path on the device, is too long for the wire, or is not readable under the node's
            granted capabilities.
    """
    if len(roots) > MAX_OBSERVE_ROOTS:
        raise WatchRootRefusedError(
            node.device_id,
            f"{len(roots)} directories were named and at most {MAX_OBSERVE_ROOTS} fit",
        )
    normal: list[str] = []
    for position, text in enumerate(roots, start=1):
        root = absolute_device_path(text)
        if root is None:
            raise WatchRootRefusedError(
                node.device_id, f"directory {position} is not an absolute path on the device"
            )
        if len(root) > MAX_OBSERVED_PATH_CHARS:
            raise WatchRootRefusedError(
                node.device_id,
                f"directory {position} is longer than {MAX_OBSERVED_PATH_CHARS} characters",
            )
        # The packet refuses a whole look at the device when one directory in it is not covered,
        # so a directory its capabilities do not cover is refused here rather than at every sample.
        if not grants_read(node.granted_capabilities, root):
            raise WatchRootRefusedError(
                node.device_id,
                f"directory {position} is not one the device's access level lets the Hive read",
            )
        normal.append(root)
    return _outermost(normal)


def _outermost(roots: list[str]) -> tuple[str, ...]:
    """Keep the first of any directories that are the same, and drop one inside another."""
    kept: list[str] = []
    for index, root in enumerate(roots):
        rule = DevicePathRule(root)  # The root's own spelling picks the case rules it is judged by.
        covered = any(
            rule.within(other, root) and (not rule.within(root, other) or other_index < index)
            for other_index, other in enumerate(roots)
            if other_index != index
        )
        if not covered:
            kept.append(root)
    return tuple(kept)
