"""Re-export HeldCells and ResumeHints: the Queen's bookkeeping about a lost device's tasks.

The Queen holds the tasks of a Swarm device that has gone away rather than failing or moving them
at once (ADR-0053), and re-places them from their last Handoff when the wait is over. The dispatcher
has to know which Cells are held, and where a re-placed task resumes from, without importing the
swarm gate that decides: these two small books are that shared knowledge, and nothing else. They
live in a package of their own because `hivemind.queen.deps` (which owns them through
`DispatchBook`) may import nothing that imports it back, and `hivemind.queen.dispatcher` does.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside the queen package. Imported
    by `hivemind.queen.deps`; read by the dispatcher and the results tick; written by the swarm
    gate and the attach path. Calls into waggle only.

Key invariants:
    - This module contains no logic of its own: it is a face over `.book` (codingrules 5.2).

See Also:
    - hivemind.queen.holds.book for the books and their invariants.
    - hivemind.queen.swarm_gate.unreachable for the decision that fills them.

Public API:
    - HeldCells: the Cells whose tasks the Queen is holding, and why (book).
    - ResumeHints: where each re-placed task resumes from at its next placement (book).
    - HeldRetries, HeldRetry: the retries a Supersedure held, for a rollback to resend (book).
"""

from hivemind.queen.holds.book import HeldCells, HeldRetries, HeldRetry, ResumeHints

__all__ = ["HeldCells", "HeldRetries", "HeldRetry", "ResumeHints"]
