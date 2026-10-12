"""Define the scaffolder's one error: a scaffold that could not be produced at all.

The Royal Jelly Lab's scaffolder asks a model to write a tool. A refusal of the spec the model
wrote is `SpecRejectedError` and an unpackable result is `PackageError` (both already in
`hivemind.royal_jelly.errors`); this module adds the third way a scaffold fails, which is that no
usable reply came back: the model was unreachable, refused, answered with malformed output, ran out
of time, or wrote text that cannot be stored. One class, so the promotion flow can catch the three
`RoyalJellyError` subclasses a scaffold can raise and turn each into a `tool.rejected` event.

Fits into the Hive:
    Layer 3 (sources of Cells, and capabilities handed down), inside royal_jelly.scaffold. Raised
    by `ModelScaffolder`; caught by the promotion flow. Calls into `hivemind.royal_jelly.errors`
    only.

Key invariants:
    - The cause is always chained (`raise ... from`), so the trail keeps the model-side reason.
    - The message names the tool and the failure class, never any text the model wrote.

See Also:
    - hivemind.royal_jelly.errors for the sibling errors a scaffold can raise.
    - hivemind.royal_jelly.scaffold.scaffolder for the raiser.
"""

from __future__ import annotations

from typing import ClassVar

from hivemind.royal_jelly.errors import RoyalJellyError

__all__ = ["ScaffoldFailedError"]


class ScaffoldFailedError(RoyalJellyError):
    """Raise when the scaffolder could not produce any reply to validate.

    Covers an unreachable or refusing model, a reply the structured ladder could not parse, a
    timeout and text that cannot be written to a file. It is never raised for a reply that parsed
    but asked for too much: that is `SpecRejectedError`.
    """

    code: ClassVar[str] = "hivemind.royal_jelly.scaffold_failed"

    def __init__(self, tool_name: str, reason: str) -> None:
        """Build the error for a scaffold that produced nothing usable.

        Args:
            tool_name: The tool the scaffold was for, so a log line names what failed.
            reason: A short phrase for the cause (the error class name, `"timed out"`).
        """
        super().__init__(f"Scaffolding tool {tool_name!r} failed: {reason}.")
        self.tool_name = tool_name
        self.reason = reason
