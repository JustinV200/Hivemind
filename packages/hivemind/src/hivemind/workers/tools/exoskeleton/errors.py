"""Define the errors an Exoskeleton tool raises when a call cannot go ahead as asked.

The Exoskeleton (the optional display, input, audio and browser peripherals of a Cell) is driven
by the tools in this package, and a model's call to one can fail before anything is proposed: an
argument that cannot become a valid GUI step or expectation, a peripheral this task never had
attached, or a read (a capture, a snapshot, a recording) that the peripheral itself failed. Each
is a `hivemind.workers.tools.errors.ToolError`, so `hivemind.workers.tools.registry.ToolRegistry.
execute` hands its message back to the model as ordinary tool-result text to act on, rather than
letting it end the Worker's tool loop. Messages never carry typed text, a frame or audio.

Fits into the Hive:
    Layer 4 (roles that do the work), inside `hivemind.workers.tools.exoskeleton`. Raised by the
    package's argument parsing, its action path and its read-only tools; caught by
    `ToolRegistry.execute`. Calls into `hivemind.workers.tools.errors` only.

Key invariants:
    - Every class here sets its own `code`; none shares one with another ToolError.
    - No message quotes an argument that could be typed text: a reason names fields and bounds.

See Also:
    - hivemind.workers.tools.errors for ToolError, this module's root.
    - .claude/codingrules.md section 10 for the exception rules, and section 12 for what a
      message may never carry.
"""

from __future__ import annotations

from typing import ClassVar

from hivemind.workers.tools.errors import ToolError

__all__ = ["GuiArgumentError", "PeripheralMissingError", "PeripheralReadError"]


class GuiArgumentError(ToolError):
    """Raise when an Exoskeleton tool's arguments cannot become a valid step or expectation."""

    code: ClassVar[str] = "hivemind.workers.tools.exoskeleton.bad_argument"

    def __init__(self, reason: str) -> None:
        """Build the error for one argument the model can correct and call again with.

        Args:
            reason: What is wrong, naming the argument and the rule it broke; never its value
                when that value could be typed text.
        """
        super().__init__(f"invalid argument: {reason}")
        self.reason = reason


class PeripheralMissingError(ToolError):
    """Raise when a tool needs a peripheral this task's Exoskeleton does not have attached."""

    code: ClassVar[str] = "hivemind.workers.tools.exoskeleton.not_attached"

    def __init__(self, peripheral: str) -> None:
        """Build the error for one peripheral that is not attached.

        Args:
            peripheral: What is missing: "Exoskeleton", "display", "input", "browser" or "audio".
        """
        super().__init__(f"no {peripheral} is attached for this task.")
        self.peripheral = peripheral


class PeripheralReadError(ToolError):
    """Raise when a read-only tool's peripheral fails: a capture, a page read, a recording."""

    code: ClassVar[str] = "hivemind.workers.tools.exoskeleton.read_failed"

    def __init__(self, tool: str, reason: str) -> None:
        """Build the error for one read that failed on its peripheral.

        Args:
            tool: The tool that was reading ("see", "browser_snapshot", "listen", ...).
            reason: The peripheral's own reason (`hivemind.exoskeleton.PeripheralError.reason`),
                which never carries a secret, a frame or audio.
        """
        super().__init__(f"{tool} failed: {reason}")
        self.tool = tool
        self.reason = reason
