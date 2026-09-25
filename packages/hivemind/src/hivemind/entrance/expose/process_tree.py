"""Define kill_process_tree: end a process and every process it started, on any platform.

The tunnel client the Entrance supervises in `tunnel` mode (ADR-0041) may start helpers of its
own (a relay, a DNS helper). On POSIX the supervisor starts the child in its own session and
signals the whole process group, so a helper dies with it; Windows has no process-group signal,
and ending the child alone (TerminateProcess) left its helpers running with the door still open.
`kill_process_tree` walks the tree with psutil, children first, then kills the child itself, so
stopping the tunnel, and so reducing the Entrance, closes every process the tunnel started.

Fits into the Hive:
    Layer 7 (edges: HTTP, dashboard), inside `hivemind.entrance.expose`. Called by
    `hivemind.entrance.expose.tunnel` on Windows. Calls into psutil only; it starts no process.

Key invariants:
    - Never raises for a process that already exited: a tree that vanished is already ended.
    - Kills descendants before the root, so a helper is never re-parented away from the walk.

See Also:
    - hivemind.entrance.expose.tunnel for the supervisor that calls it.
"""

from __future__ import annotations

import psutil

from hivemind.common.logging import get_logger

log = get_logger(__name__)

__all__ = ["kill_process_tree"]


def kill_process_tree(pid: int) -> None:
    """Kill `pid` and every process it started, descendants first.

    Args:
        pid: The root of the tree: a child the caller started and has not reaped yet (a reaped
            child's pid may already belong to an unrelated process).
    """
    try:
        root = psutil.Process(pid)
        members = [*root.children(recursive=True), root]
    except psutil.NoSuchProcess:
        # The root already exited: whatever it started was re-parented or has gone with it.
        log.debug("entrance.process_tree_gone", pid=pid)
        return
    # Descendants first: a helper that outlived its parent would be re-parented out of reach.
    for member in members:
        try:
            member.kill()
        except psutil.NoSuchProcess:
            # It exited on its own between the walk and the kill: nothing is left to end.
            log.debug("entrance.process_tree_member_gone", pid=member.pid)
