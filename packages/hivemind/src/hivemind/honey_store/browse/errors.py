"""Define the Honey browser's own errors: a path it cannot parse, nothing visible there, bad input.

The Honey browser presents the Honey Store (the Hive's cold-tier knowledge base: Nectar, raw
findings, ripened into Honey, searchable knowledge) as a read-only folder tree an operator walks
by path. Every error it raises on purpose roots in `hivemind.honey_store.errors.HoneyStoreError`,
so a caller that already handles any Honey Store failure handles these too, and each class fixes
its own stable `code` (codingrules section 10: a caller matches on `code`, never on the message).

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy), inside the honey_store package's
    browse sub-package. Raised by `hivemind.honey_store.browse.paths`, `.documents`, `.folders`,
    `.notes`, `.relabel` and `.browser`; caught by `hive honey` (and, later, the Observation
    Hive), which turns each into one clean line for the operator.

Key invariants:
    - `BrowseNotFoundError` never says whether something exists but is hidden from this reader or
      does not exist at all: a reader learns nothing about what its scope or clearance forbids.
    - Every class here sets its own `code`; no two share one.

See Also:
    - .claude/codingrules.md section 10 for the error rules this module follows.
    - hivemind.honey_store.errors for the package's root, HoneyStoreError.
"""

from __future__ import annotations

from typing import ClassVar

from hivemind.honey_store.errors import HoneyStoreError

__all__ = ["BrowseError", "BrowseInputError", "BrowseNotFoundError", "BrowsePathError"]


class BrowseError(HoneyStoreError):
    """Root of every error the Honey browser raises on purpose; catch it to handle any of them."""

    code: ClassVar[str] = "hivemind.honey_store.browse_error"


class BrowsePathError(BrowseError):
    """Raise when a path names no place in the folder tree, or the wrong kind of place for a call.

    `ls` on a path the grammar does not know, `cat` on a folder, `search` in a folder that holds
    no Honey (live Cell Wax, Bee Bread), a note proposed from a Cell folder whose id is not a Cell
    id: each is the caller asking for something the tree cannot give, not a missing item.
    """

    code: ClassVar[str] = "hivemind.honey_store.browse_path_invalid"

    def __init__(self, path: str, why: str) -> None:
        """Build the error for a path the requested operation cannot use.

        Args:
            path: The path as the caller gave it.
            why: One clause saying what is wrong with it.
        """
        super().__init__(f"Honey path {path!r} cannot be used here: {why}.")
        self.path = path
        self.why = why


class BrowseNotFoundError(BrowseError):
    """Raise when a document path shows nothing this reader may see.

    Covers a row that does not exist, one the reader's `honey:read` capabilities or clearance
    ceiling hide, and one no longer live (tainted or retired), all with the same message, so a
    reader cannot probe for what it may not read.
    """

    code: ClassVar[str] = "hivemind.honey_store.browse_not_found"

    def __init__(self, path: str) -> None:
        """Build the error for a document path with nothing visible at it.

        Args:
            path: The document path that was asked for.
        """
        super().__init__(f"Nothing at Honey path {path!r} is visible to this reader.")
        self.path = path


class BrowseInputError(BrowseError):
    """Raise when a proposed note or a relabel carries a field the Hive would refuse later."""

    code: ClassVar[str] = "hivemind.honey_store.browse_input_invalid"

    def __init__(self, field: str, why: str) -> None:
        """Build the error for one bad input field.

        Args:
            field: The field's name (`title`, `text`, `reason`).
            why: One clause saying what is wrong with it.
        """
        super().__init__(f"The {field} is not usable: {why}.")
        self.field = field
        self.why = why
