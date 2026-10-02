"""Hold the few printing pieces every `hive honey` renderer shares: placeholders and two formats.

`hivemind.cli.honey.render` prints what the Honey Store returns, split by what is printed
(`browse`, `store`, `review`). Each part writes an empty field the same way, indents a
continuation line the same way, and renders provenance and `name=count` pairs the same way, so
those live here once rather than in whichever part happened to need them first.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside `hivemind.cli.honey.render`. Imported by
    its sibling modules only; not part of the package's face. Calls into `waggle.messages.honey`
    (HoneyProvenance) only.

Key invariants:
    - Pure formatting: every function returns a string and prints nothing.

See Also:
    - hivemind.cli.honey.render for the package these helpers serve.
"""

from __future__ import annotations

from waggle.messages.honey import HoneyProvenance

NONE = "-"  # Printed for a field that has no value.
INDENT = "    "  # A continuation line under one listed entry or hit.

__all__ = ["INDENT", "NONE", "counts", "provenance"]


def counts(values: dict[str, int]) -> str:
    """Render `{name: count}` as `name=count` pairs, sorted by name; `-` when empty."""
    # Nothing of this kind at all: a visible dash, never a blank line.
    if not values:
        return NONE
    return "  ".join(f"{name}={count}" for name, count in sorted(values.items()))


def provenance(source: HoneyProvenance) -> str:
    """Render where an item came from: task, Cell, bee and when."""
    return (
        f"task={source.task_id or NONE}  cell={source.cell_id or NONE}  "
        f"bee={source.bee or NONE}  observed={source.observed_at.isoformat()}"
    )
