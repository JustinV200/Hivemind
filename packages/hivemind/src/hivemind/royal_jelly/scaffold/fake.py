"""Provide FakeScaffolder: a Scaffolder that returns scripted results, for tests and dry runs.

The promotion flow and the Queen's tool job both depend on a `Scaffolder`. This fake lets a test
decide exactly what a scaffold produces, or fail, without a model: give it a result per call (the
first call gets the first, the retry the second), or a callable that builds the result from the
brief it is handed. It records every brief it saw, so a test can assert the retry carried the
failing report.

Fits into the Hive:
    Layer 3 (sources of Cells, and capabilities handed down), inside royal_jelly.scaffold. Used by
    the promotion flow's tests and the later waves' tests; it is production-quality code only in
    the sense of being typed and documented, never wired by a composition root.

Key invariants:
    - `briefs` lists every brief passed to `scaffold`, in order.
    - A script shorter than the number of calls repeats its last entry, so a test that scripts
      one result gets it on the retry too.

See Also:
    - hivemind.royal_jelly.scaffold.scaffolder for the Protocol this satisfies.
    - codingrules section 14.4 (fakes over mocks).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

from hivemind.royal_jelly.scaffold.models import ScaffoldResult, ToolRequestBrief

__all__ = ["FakeScaffolder", "ScaffoldScript"]

# A script is a fixed sequence of results (or errors), or a function of the brief.
type ScaffoldScript = (
    Sequence[ScaffoldResult | Exception] | Callable[[ToolRequestBrief], ScaffoldResult]
)


class FakeScaffolder:
    """A `Scaffolder` that answers from a script and remembers what it was asked."""

    def __init__(self, script: ScaffoldScript) -> None:
        """Build the fake over a script.

        Args:
            script: Results (or exceptions to raise) returned one per call, the last repeating; or
                a callable building a result from the brief.
        """
        self._script = script
        self.briefs: list[ToolRequestBrief] = []

    async def scaffold(self, brief: ToolRequestBrief) -> ScaffoldResult:
        """Record `brief` and return (or raise) the scripted outcome for this call."""
        self.briefs.append(brief)
        if callable(self._script):
            return self._script(brief)
        # The Nth call gets the Nth entry; past the end the last entry repeats.
        entry = self._script[min(len(self.briefs), len(self._script)) - 1]
        if isinstance(entry, Exception):
            raise entry
        return entry
