"""Watch the device read-only from fixed collectors: processes, resources, logs and file changes.

A Warden (the per-Cell supervisor) watches an idle Real Cell (an existing device the Hive borrows
for a task) by asking the Pollen Packet (the thin gateway program on the device) what the device
looks like (ADR-0054). ``executor`` answers ``swarm.observe``: it checks the node's ``watch``
capability and the read capability of each root, then runs the collectors the request chose;
``system`` collects the process list and the resource sample through psutil, and ``files``
collects the tail of log files and the file changes under named roots. None runs a command a
request could shape, none needs a lease, and none writes.

Fits into the Hive:
    Parallel to hivemind's layer table, inside the pollen package. Used by the composition root
    and pollen.agent.dispatch; calls into psutil, waggle and the standard library only.

Key invariants:
    - Read-only by construction: the collectors read platform facilities and files, nothing else.
    - The capability check runs before any collector does.

See Also:
    - docs/waggle/gateway-contract.md section 9.4 for the answer and its bounds.
    - docs/adr/0054-access-levels-watch-mode-and-the-patrol.md for why watching is read-only.

Public API:
    - ObserveExecutor: the DeviceExecutor that answers ``swarm.observe``.
"""

from pollen.executors.observe.executor import ObserveExecutor

__all__ = ["ObserveExecutor"]
