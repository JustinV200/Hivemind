"""Hold the spec's catalogue table and the registry to each other: the spec drift test.

Codingrules 7.7 makes docs/waggle/spec.md the source of truth for message shapes and asks that
the models in packages/waggle be checked against it in CI. This module parses the catalogue
table of the spec's section 8 (one row per kind: kind, class, shape, replies to, direction,
summary) and compares it with the registry in both directions, so a kind added, renamed or
reshaped on either side without the other fails CI (spec section 11). The table is read from
the markdown itself, never from a copy, so the check cannot drift from the document it guards.

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Reads docs/waggle/spec.md relative to this
    file and waggle.messages.registry; nothing depends on it.

Key invariants:
    - None: this module holds tests only.

See Also:
    - docs/waggle/spec.md section 8 (the table) and section 11 (this test's contract).
    - waggle.messages.catalogue for the code side of the same list.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import pytest

from waggle.messages.registry import all_kinds, spec_for

# packages/waggle/tests/test_spec_drift.py -> tests -> waggle -> packages -> the repository root.
SPEC_PATH = Path(__file__).resolve().parents[3] / "docs" / "waggle" / "spec.md"
CATALOGUE_HEADING = "## 8. Message catalogue"  # The section whose first table is the catalogue.
TABLE_HEADER = "| Kind | Class | Shape | Replies to | Direction | Summary |"
COLUMN_COUNT = 6  # The header's columns; a row with any other count is a broken table.
EXPECTED_ROWS = 66  # The catalogue's size in protocol 1.0; a new kind bumps this with the spec.

_KIND_CELL = re.compile(r"^`([a-z][a-z_]*\.[a-z][a-z_]*)`$")  # `<family>.<snake_name>`
_CLASS_CELL = re.compile(r"^`([A-Z][A-Za-z]*)`$")  # `PascalCase`, the class name.


@dataclass(frozen=True, slots=True)
class CatalogueRow:
    """The registry-facing columns of one table row: kind, class name, shape, replies_to."""

    kind: str
    class_name: str
    shape: str
    replies_to: str | None


@pytest.fixture(scope="module")
def rows() -> tuple[CatalogueRow, ...]:
    """The catalogue table's data rows, parsed once for the module."""
    return _catalogue_rows(SPEC_PATH.read_text(encoding="utf-8"))


def test_the_catalogue_table_has_exactly_sixty_six_rows(rows: tuple[CatalogueRow, ...]) -> None:
    assert len(rows) == EXPECTED_ROWS


def test_every_table_row_is_registered_with_the_same_class_shape_and_reply(
    rows: tuple[CatalogueRow, ...],
) -> None:
    registered = set(all_kinds())
    missing = [row.kind for row in rows if row.kind not in registered]

    assert not missing, f"Kinds in the spec's catalogue table but not in the registry: {missing}"

    mismatched = [
        f"{row.kind}: table says {row.class_name}/{row.shape}/{row.replies_to}, registry says "
        f"{spec.model.__name__}/{spec.shape.value}/{spec.replies_to}"
        for row in rows
        if (spec := spec_for(row.kind))
        and (row.class_name, row.shape, row.replies_to)
        != (spec.model.__name__, spec.shape.value, spec.replies_to)
    ]
    assert not mismatched, "Rows whose class, shape or replies-to differ from the registry:\n" + (
        "\n".join(mismatched)
    )


def test_every_registered_kind_is_a_table_row(rows: tuple[CatalogueRow, ...]) -> None:
    in_table = {row.kind for row in rows}
    unlisted = [kind for kind in all_kinds() if kind not in in_table]

    assert not unlisted, f"Kinds in the registry but not in the spec's catalogue table: {unlisted}"


def test_the_table_and_the_registry_list_kinds_in_the_same_order(
    rows: tuple[CatalogueRow, ...],
) -> None:
    assert [row.kind for row in rows] == list(all_kinds())


def _catalogue_rows(text: str) -> tuple[CatalogueRow, ...]:
    """Parse the catalogue table: the first table after the section 8 heading with the header."""
    lines = text.splitlines()
    # The heading first, then the header row: the section opens with prose before the table.
    start = lines.index(CATALOGUE_HEADING)
    header = next(
        index for index in range(start, len(lines)) if lines[index].strip() == TABLE_HEADER
    )
    parsed: list[CatalogueRow] = []
    # Data rows run from the line after the |---| separator to the first line that is not a
    # table row; markdown ends a table at the first blank or non-pipe line.
    for line in lines[header + 2 :]:
        if not line.startswith("|"):
            break
        parsed.append(_parse_row(line))
    return tuple(parsed)


def _parse_row(line: str) -> CatalogueRow:
    """Turn one `| kind | Class | shape | replies | ... |` line into a CatalogueRow."""
    cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
    if len(cells) != COLUMN_COUNT:
        raise ValueError(f"Catalogue row has {len(cells)} columns, not {COLUMN_COUNT}: {line!r}")
    kind = _KIND_CELL.match(cells[0])
    class_name = _CLASS_CELL.match(cells[1])
    if kind is None or class_name is None:
        raise ValueError(f"Catalogue row's kind or class is not a backticked name: {line!r}")
    # "none" is the table's spelling of no reply target; anything else must be a backticked kind.
    replies_to: str | None = None
    if cells[3] != "none":
        target = _KIND_CELL.match(cells[3])
        if target is None:
            raise ValueError(f"Catalogue row's replies-to is neither `kind` nor none: {line!r}")
        replies_to = target.group(1)
    return CatalogueRow(kind.group(1), class_name.group(1), cells[2], replies_to)
