"""Define ToolError: the root a Worker's built-in tool raises when it must stop, not just report.

Most failures a tool meets are not exceptional at all: an unknown tool, a schema mismatch, a
rejected Capping proposal are all reported as tool-result text (codingrules section 15: "LLM output
is untrusted input... never used... without validation" -- a model reads the failure and tries
again, exactly like a real command that printed an error to stderr). `ToolError` exists for the
one case that is genuinely exceptional: a path this Worker's session cannot even reach, which is a
session/lease boundary a capability grant cannot paper over. `hivemind.workers.tools.registry.
ToolRegistry.execute` catches `ToolError` and turns it into its message, so raising one here still
reaches the model as text rather than crashing the tool loop; a `HandoffRequestedError` or
`hivemind.workers.errors.WorkerCancelledError` is a different kind of stop (a control exception)
and is never wrapped in this tree.

Fits into the Hive:
    Layer 4 (roles that do the work), inside `hivemind.workers.tools`. Raised by
    `hivemind.workers.tools.session`'s `read_file`/`write_file` when a path is unreachable from the
    session; caught by `hivemind.workers.tools.registry.ToolRegistry.execute`. Calls into
    `hivemind.workers.errors` only.

Key invariants:
    - Every ToolError subclass sets its own `code`; none shares a code with another.
    - UnreachablePathError always names the path, so the message a model reads is specific enough
      to act on without a stack trace.

See Also:
    - .claude/codingrules.md section 10 for the exceptions and errors rules this module follows.
    - .claude/codingrules.md section 15 for "LLM output is untrusted input".
    - hivemind.workers.errors for WorkerError, this module's own root.
    - hivemind.workers.tools.registry for ToolRegistry.execute, this error tree's one catcher.
"""

from __future__ import annotations

from typing import ClassVar

from hivemind.workers.errors import WorkerError

__all__ = ["ToolError", "UnreachablePathError"]


class ToolError(WorkerError):
    """Root of every error a Worker's built-in tool raises on purpose.

    Subclass this for a specific failure, as `UnreachablePathError` does; code that has nothing
    more specific to say may raise this directly.
    """

    code: ClassVar[str] = "hivemind.workers.tools.error"


class UnreachablePathError(ToolError):
    """Raise when a tool's session cannot reach a path at all, not merely lacks a capability for it.

    `hivemind.cell.session.resolve_scratch_path` (and every `CellSession.get_file`/`put_file`
    built on it) raises `hivemind.cell.errors.PathNotAllowedError` for exactly this case; a tool
    catches that and re-raises this instead, so the failure reaches the model as ordinary
    tool-result text through `hivemind.workers.tools.registry.ToolRegistry.execute` rather than
    propagating as an unhandled `hivemind.cell` error.
    """

    code: ClassVar[str] = "hivemind.workers.tools.unreachable_path"

    def __init__(self, path: str) -> None:
        """Build the error for a path this Worker's session cannot reach at all.

        Args:
            path: The path a tool was asked to touch, as the model wrote it.
        """
        super().__init__(
            f"{path!r} is not reachable from this Worker's session (outside scratch and outside "
            "every path its lease allows)."
        )
        self.path = path
