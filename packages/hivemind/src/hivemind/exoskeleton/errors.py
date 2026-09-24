"""Define ExoskeletonError and the ways an Exoskeleton peripheral can fail on purpose.

The Exoskeleton is the optional set of peripherals a Cell (a unit of compute a Worker runs in or
on) can be given for one task: a display to see (CompoundEye), a pointer and keyboard to drive
(Antennae), audio in and out (Buzz), and a browser (the fast path). This module is the package's
own error tree, rooted at `ExoskeletonError` (codingrules section 10), so a caller several layers
up can catch one name. `AttachError` is attach refusing or failing to equip a Cell: the plan could
not be met (no display to use or start, a peripheral the bee holds no capability for) or a process
it started never became ready; attach has already stopped whatever it started when this is raised.
`PeripheralError` is a peripheral that was attached failing one operation: its command exited
non-zero, timed out, or its process is gone. `ElementNotFoundError` is the browser failing to find
the element a step names, the one failure a bee most often corrects by trying a different target.

Fits into the Hive:
    Layer 3 (sources of Cells, and capabilities handed down). Raised by every module under
    `hivemind.exoskeleton` and read by the Worker tools that drive a peripheral
    (`hivemind.workers.tools`) and the Capping gate's GUI surface, which turns an apply-time failure
    into an ordinary failed apply rather than a crash.

Key invariants:
    - Every ExoskeletonError subclass sets its own `code`; none shares a code with another.
    - Messages name the peripheral and the operation, never the text a bee typed or a frame.

See Also:
    - .claude/codingrules.md section 10 for the exceptions and errors rules this module follows.
    - docs/adr/0031-exoskeleton-on-x11-with-playwright-fast-path.md for what attach may refuse.
    - hivemind.common.errors for HiveMindError, the root this tree descends from.
"""

from __future__ import annotations

from typing import ClassVar

from hivemind.common.errors import HiveMindError

__all__ = [
    "AttachError",
    "ElementNotFoundError",
    "ExoskeletonError",
    "PeripheralError",
    "RecordingNotFoundError",
]


class ExoskeletonError(HiveMindError):
    """Root of every error `hivemind.exoskeleton` raises on purpose."""

    code: ClassVar[str] = "hivemind.exoskeleton.error"


class AttachError(ExoskeletonError):
    """Raise when attach cannot equip a Cell with what its task needs.

    Either the plan could not be met from the Cell's capabilities and the bee's grants, or a
    process attach started never became ready. Everything attach started is already stopped.
    """

    code: ClassVar[str] = "hivemind.exoskeleton.attach_failed"

    def __init__(self, reason: str) -> None:
        """Build the error for one attach that could not complete.

        Args:
            reason: Why, one sentence naming the peripheral and the missing capability or the
                process that failed.
        """
        super().__init__(f"Could not attach the Exoskeleton: {reason}")
        self.reason = reason


class PeripheralError(ExoskeletonError):
    """Raise when an attached peripheral fails one operation: an error exit, a timeout, a crash."""

    code: ClassVar[str] = "hivemind.exoskeleton.peripheral_failed"

    def __init__(self, peripheral: str, operation: str, reason: str) -> None:
        """Build the error for one failed peripheral operation.

        Args:
            peripheral: Which peripheral failed ("compound_eye", "antennae", "buzz", "browser").
            operation: What it was doing ("capture", "click", "listen", "navigate", ...).
            reason: Why, from the tool's own exit or the library's error; never a secret.
        """
        super().__init__(f"The {peripheral} could not {operation}: {reason}")
        self.peripheral = peripheral
        self.operation = operation
        self.reason = reason


class ElementNotFoundError(PeripheralError):
    """Raise when the browser cannot find the element a step names within its wait."""

    code: ClassVar[str] = "hivemind.exoskeleton.element_not_found"

    def __init__(self, target: str, operation: str) -> None:
        """Build the error for one element that was not there.

        Args:
            target: The element, as `ElementTarget.describe()` renders it.
            operation: What the browser was asked to do to it.
        """
        super().__init__("browser", operation, f"no element matches {target}")
        self.target = target


class RecordingNotFoundError(ExoskeletonError):
    """Raise when a flight recording is looked up by an id no store holds."""

    code: ClassVar[str] = "hivemind.exoskeleton.recording_not_found"

    def __init__(self, recording_id: str) -> None:
        """Build the error for one missing recording.

        Args:
            recording_id: The id that was looked up.
        """
        super().__init__(f"No flight recording with id {recording_id!r} exists.")
        self.recording_id = recording_id
