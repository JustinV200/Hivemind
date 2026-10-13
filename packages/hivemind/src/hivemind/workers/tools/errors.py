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

__all__ = ["PackageUnavailableError", "ToolError", "ToolsChangedError", "UnreachablePathError"]


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


class PackageUnavailableError(ToolError):
    """Raise when a `PackageSource` cannot hand over the bytes of a package it was asked for.

    The source behind a Worker's Comb tools (the resident Basket) raises this for a blob it does
    not hold or cannot read. `hivemind.workers.tools.comb.invoke.CombToolRunner` turns it into a
    `tool.load_refused` event and a readable line; the call never runs.
    """

    code: ClassVar[str] = "hivemind.workers.tools.package_unavailable"

    def __init__(self, sha256: str, reason: str) -> None:
        """Build the error for a package the source could not supply.

        Args:
            sha256: The hash asked for (a hash names one immutable version everywhere).
            reason: One short phrase for why (`"not resident"`, `"unreadable"`).
        """
        super().__init__(f"The package {sha256[:16]} is unavailable: {reason}.")
        self.sha256 = sha256


class ToolsChangedError(WorkerError):
    """Raise between tool calls when the Warden changed this Worker's Comb tool bundle.

    A control exception, not a `ToolError`: `ToolRegistry.execute` lets it propagate, exactly as it
    does `HandoffRequestedError`. `hivemind.workers.roles.bounded_loop.executor.LoopExecutor`
    raises it once a call has finished and `ctx.comb_tools.version` is no longer the version the
    registry was built from; `run_bounded_loop` is its one catcher, which rebuilds the registry
    and runs the loop again from state, so the model's next round is offered the new tools.
    """

    code: ClassVar[str] = "hivemind.workers.tools.tools_changed"

    def __init__(self, version: int) -> None:
        """Build the error naming the bundle version that is now current.

        Args:
            version: `CombToolBundle.version` at the moment the change was noticed.
        """
        super().__init__(
            f"This Worker's Comb tool bundle changed (now version {version}); the tools offered "
            "to the model must be rebuilt."
        )
        self.version = version
