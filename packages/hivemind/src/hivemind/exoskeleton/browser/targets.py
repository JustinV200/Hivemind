"""State the rule every browser applies to an ElementTarget: it must name exactly one element.

A GUI step names the element it acts on by accessible role and name, label, visible text or CSS
selector (waggle's `ElementTarget`). A browser resolves it against the elements a person could see
right now, hidden ones never count, and then insists on exactly one: none is an
`ElementNotFoundError` the bee corrects by naming something else, and more than one is refused
rather than guessed at, because clicking the first of two "Delete" buttons is how a wrong click
happens. This module holds the parts of that rule both browsers (the Playwright one and the fake)
must word identically: the peripheral name their errors carry, the refusal of an ambiguous target,
and what "exact" means for a name or a text (equal once whitespace is collapsed, as Playwright's
own exact matching compares).

Fits into the Hive:
    Layer 3 (sources of Cells, and capabilities handed down), inside `hivemind.exoskeleton.browser`.
    Called by `browser.playwright` and `browser.fake`. Calls into `hivemind.exoskeleton.errors` and
    waggle's `ElementTarget` only.

Key invariants:
    - An ambiguity error names the target and the count, never any page content.
    - `normalised` is idempotent: normalising twice changes nothing.

See Also:
    - waggle.messages.capping.gui for ElementTarget.
    - hivemind.exoskeleton.errors for ElementNotFoundError and PeripheralError.
"""

from __future__ import annotations

from hivemind.exoskeleton.errors import PeripheralError
from waggle.messages.capping import ElementTarget

PERIPHERAL = "browser"  # How every browser error names the peripheral that failed.

__all__ = ["PERIPHERAL", "ambiguity", "normalised"]


def ambiguity(target: ElementTarget, count: int, operation: str) -> PeripheralError:
    """Build the error for a target that names more than one visible element.

    Args:
        target: The target that matched too much.
        count: How many visible elements it matched; more than one.
        operation: What the browser was asked to do ("click", "fill", ...).

    Returns:
        The PeripheralError to raise, naming the target and the count.
    """
    return PeripheralError(
        PERIPHERAL,
        operation,
        f"{target.describe()} matches {count} elements; name exactly one of them",
    )


def normalised(text: str) -> str:
    """Collapse every run of whitespace to one space and trim the ends, for exact comparison.

    Args:
        text: An accessible name, a label or an element's visible text.

    Returns:
        The text as an exact match compares it.

    Example:
        >>> normalised("  Log   in ")
        'Log in'
    """
    return " ".join(text.split())
