"""Parse a Honey browser path into what it names: the root, a folder, a scope, or one document.

The Honey browser presents the Honey Store (the Hive's cold-tier knowledge base) as a read-only
folder tree (roadmap 7.10): `/hive` holds shared knowledge; `/cells/<cell id>` one Cell's own
history, with `/cells/<cell id>/wax` for that Cell's live Cell Wax (the Queen's standing cautions
about it, which live in memory, never as Honey rows); `/bees/<bee id>` one bee's own material;
`/tasks/<task id>` one task's working material; `/bee-bread` recent Bee Bread (the warm memory
tier, not yet or never ripened). `parse_path` is the one grammar every browser operation shares:
it turns text into a `BrowsePath` naming its `PathKind`, its scope and its item id, or refuses it.
The scope half of every folder comes from `hivemind.honey_store.scope.scope_for_folder`, so a
folder and the scope whose rows it shows can never disagree.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy), inside the honey_store package's
    browse sub-package. Called by `hivemind.honey_store.browse.browser`, `.folders`,
    `.documents`, `.notes` and `.relabel`. Calls into `hivemind.honey_store.scope` and this
    package's errors only.

Key invariants:
    - A path parses to exactly one PathKind or raises BrowsePathError; "." and ".." never
      navigate, and a doubled or trailing "/" never changes what a path names.
    - Every SCOPE, HONEY, WAX_FOLDER and WAX_NOTE path carries a scope `folder_for_scope` maps
      straight back to its folder.

See Also:
    - hivemind.honey_store.scope for folder_for_scope/scope_for_folder, the folder<->scope rule.
    - .claude/roadmap.md step 7.10 for the folder tree this grammar implements.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import Enum

from hivemind.honey_store.browse.errors import BrowsePathError
from hivemind.honey_store.scope import HIVE_SCOPE, folder_for_scope, scope_for_folder

ROOT_PATH = "/"  # The tree's root: lists the five top-level folders.
HIVE_FOLDER = "hive"  # Shared knowledge: the `hive` scope's rows.
CELLS_FOLDER = "cells"  # One folder per Cell scope that holds Honey.
BEES_FOLDER = "bees"  # One folder per bee scope that holds Honey.
TASKS_FOLDER = "tasks"  # One folder per task scope that holds Honey.
BEE_BREAD_FOLDER = "bee-bread"  # Recent Bee Bread, the warm tier.
WAX_FOLDER = "wax"  # Inside a Cell's folder: its live Cell Wax.
# The root's own listing, in the order the roadmap names the folders.
TOP_FOLDERS = (HIVE_FOLDER, CELLS_FOLDER, BEES_FOLDER, TASKS_FOLDER, BEE_BREAD_FOLDER)
MAX_PATH_CHARS = 512  # A folder and two ids with room to spare; a longer path is not a path.
CELL_SCOPE_KIND = "cell"  # The scope kind of one Cell's own history (hivemind.honey_store.scope).
CELL_SCOPE_PREFIX = f"{CELL_SCOPE_KIND}:"  # Every Cell scope starts with this.
_SEPARATOR = "/"  # Between segments, as on a filesystem; the tree is walked the same way.
_NAVIGATION_SEGMENTS = frozenset({".", ".."})  # Relative navigation: never meaningful here.
# An index folder's name -> the scope kind every folder inside it shows (`cells` -> `cell:<id>`).
_INDEX_SCOPE_KINDS = {CELLS_FOLDER: CELL_SCOPE_KIND, BEES_FOLDER: "bee", TASKS_FOLDER: "task"}

__all__ = [
    "BEES_FOLDER",
    "BEE_BREAD_FOLDER",
    "CELLS_FOLDER",
    "CELL_SCOPE_KIND",
    "CELL_SCOPE_PREFIX",
    "HIVE_FOLDER",
    "MAX_PATH_CHARS",
    "ROOT_PATH",
    "TASKS_FOLDER",
    "TOP_FOLDERS",
    "WAX_FOLDER",
    "BrowsePath",
    "PathKind",
    "bee_bread_entry_path",
    "index_path",
    "parse_path",
    "wax_folder_path",
    "wax_note_path",
]


class PathKind(Enum):
    """What one browser path names; decides which operations it supports."""

    ROOT = "ROOT"  # "/": the five top-level folders.
    SCOPE_INDEX = "SCOPE_INDEX"  # "/cells", "/bees", "/tasks": one folder per scope with Honey.
    SCOPE = "SCOPE"  # "/hive", "/cells/<id>", "/bees/<id>", "/tasks/<id>": one scope's rows.
    HONEY = "HONEY"  # "<scope folder>/<honey id>": one Honey row.
    WAX_FOLDER = "WAX_FOLDER"  # "/cells/<id>/wax": that Cell's live Cell Wax.
    WAX_NOTE = "WAX_NOTE"  # "/cells/<id>/wax/<wax id>": one live Cell Wax note.
    BEE_BREAD = "BEE_BREAD"  # "/bee-bread": recent Bee Bread entries.
    BEE_BREAD_ENTRY = "BEE_BREAD_ENTRY"  # "/bee-bread/<entry id>": one Bee Bread entry.


# The kinds that are folders (`ls` lists their contents); every other kind is one document.
_FOLDER_KINDS = frozenset(
    {
        PathKind.ROOT,
        PathKind.SCOPE_INDEX,
        PathKind.SCOPE,
        PathKind.WAX_FOLDER,
        PathKind.BEE_BREAD,
    }
)


@dataclass(frozen=True, slots=True)
class BrowsePath:
    """One parsed browser path: what it names, and the scope and item id it carries."""

    kind: PathKind  # What the path names.
    path: str  # The normalised path: one leading "/", no doubled or trailing "/".
    scope: str | None = None  # SCOPE, HONEY, WAX_FOLDER, WAX_NOTE: the scope it lives in.
    scope_kind: str | None = None  # SCOPE_INDEX only: "cell", "bee" or "task".
    item_id: str | None = None  # HONEY, WAX_NOTE, BEE_BREAD_ENTRY: the document's own id.

    @property
    def is_folder(self) -> bool:
        """Whether this path is a folder (`ls` lists it) rather than one document (`cat`)."""
        return self.kind in _FOLDER_KINDS


def parse_path(raw: str) -> BrowsePath:
    """Parse one browser path.

    Args:
        raw: The path as a caller typed it; a missing leading "/" is added, and a doubled or
            trailing "/" is ignored (`hive/` names `/hive`).

    Returns:
        What the path names.

    Raises:
        BrowsePathError: The path is too long, navigates with "." or "..", names no top-level
            folder, carries an id no scope accepts, or goes deeper than the tree does.
    """
    segments = _segments(raw)
    # "/" itself, however it was spelled.
    if not segments:
        return BrowsePath(kind=PathKind.ROOT, path=ROOT_PATH)
    head, rest = segments[0], segments[1:]
    parser = _TOP_PARSERS.get(head)
    if parser is not None:
        return parser(raw, rest)
    # `cells`, `bees` and `tasks` share one shape: an index of scope folders.
    if head in _INDEX_SCOPE_KINDS:
        return _parse_scoped(raw, head, rest)
    raise BrowsePathError(raw, f"there is no top-level folder {head!r}")


def index_path(scope_kind: str) -> str:
    """Return the index folder listing every scope of one kind.

    Args:
        scope_kind: "cell", "bee" or "task".

    Returns:
        "/cells", "/bees" or "/tasks".
    """
    folder = next(name for name, kind in _INDEX_SCOPE_KINDS.items() if kind == scope_kind)
    return f"{_SEPARATOR}{folder}"


def wax_folder_path(cell_scope: str) -> str:
    """Return the folder holding one Cell's live Cell Wax.

    Args:
        cell_scope: The Cell's scope, `cell:<id>`.

    Returns:
        "/cells/<id>/wax".
    """
    return f"{folder_for_scope(cell_scope)}{_SEPARATOR}{WAX_FOLDER}"


def wax_note_path(cell_scope: str, wax_id: str) -> str:
    """Return one live Cell Wax note's own path.

    Args:
        cell_scope: The Cell's scope, `cell:<id>`.
        wax_id: The note's own id.

    Returns:
        "/cells/<id>/wax/<wax id>".
    """
    return f"{wax_folder_path(cell_scope)}{_SEPARATOR}{wax_id}"


def bee_bread_entry_path(entry_id: str) -> str:
    """Return one Bee Bread entry's own path.

    Args:
        entry_id: The entry's own id.

    Returns:
        "/bee-bread/<entry id>".
    """
    return f"{_SEPARATOR}{BEE_BREAD_FOLDER}{_SEPARATOR}{entry_id}"


def _segments(raw: str) -> tuple[str, ...]:
    """Split `raw` into its non-empty segments, refusing an oversized or navigating path."""
    # Bounds what a caller can hand the parser, and what an error message repeats back.
    if len(raw) > MAX_PATH_CHARS:
        raise BrowsePathError(raw[:MAX_PATH_CHARS], f"it is longer than {MAX_PATH_CHARS} chars")
    segments = tuple(segment for segment in raw.split(_SEPARATOR) if segment)
    # The tree has no current or parent folder to move through; refusing beats guessing.
    if any(segment in _NAVIGATION_SEGMENTS for segment in segments):
        raise BrowsePathError(raw, "'.' and '..' do not navigate the Honey tree")
    return segments


def _normalised(segments: Sequence[str]) -> str:
    """Join `segments` into the one spelling every BrowsePath carries."""
    return _SEPARATOR + _SEPARATOR.join(segments)


def _parse_hive(raw: str, rest: tuple[str, ...]) -> BrowsePath:
    """Parse a path under `/hive`: the folder itself, or one Honey row in it."""
    # "/hive" itself: the shared scope's own folder.
    if not rest:
        return BrowsePath(kind=PathKind.SCOPE, path=_normalised((HIVE_FOLDER,)), scope=HIVE_SCOPE)
    # "/hive/<honey id>": one of its rows.
    if len(rest) == 1:
        path = _normalised((HIVE_FOLDER, rest[0]))
        return BrowsePath(kind=PathKind.HONEY, path=path, scope=HIVE_SCOPE, item_id=rest[0])
    raise BrowsePathError(raw, "/hive holds Honey rows directly, with no deeper folders")


def _parse_bee_bread(raw: str, rest: tuple[str, ...]) -> BrowsePath:
    """Parse a path under `/bee-bread`: the folder itself, or one entry in it."""
    # "/bee-bread" itself: recent entries, which belong to no one scope folder.
    if not rest:
        return BrowsePath(kind=PathKind.BEE_BREAD, path=_normalised((BEE_BREAD_FOLDER,)))
    # "/bee-bread/<entry id>": one entry, looked up by id.
    if len(rest) == 1:
        path = bee_bread_entry_path(rest[0])
        return BrowsePath(kind=PathKind.BEE_BREAD_ENTRY, path=path, item_id=rest[0])
    raise BrowsePathError(raw, "/bee-bread holds entries directly, with no deeper folders")


def _parse_scoped(raw: str, head: str, rest: tuple[str, ...]) -> BrowsePath:
    """Parse a path under `/cells`, `/bees` or `/tasks`: the index, a scope, or one document."""
    scope_kind = _INDEX_SCOPE_KINDS[head]
    # The index itself: one folder per scope of this kind.
    if not rest:
        return BrowsePath(
            kind=PathKind.SCOPE_INDEX, path=index_path(scope_kind), scope_kind=scope_kind
        )
    folder = _normalised((head, rest[0]))
    scope = scope_for_folder(folder)
    # scope_for_folder accepts only the id shape a stored scope may carry (letters, digits, "_"
    # and "-"), so a bad id is refused here rather than silently listing an empty folder.
    if scope is None:
        raise BrowsePathError(raw, f"{rest[0]!r} is not a valid {scope_kind} id")
    return _parse_in_scope(raw, scope, rest[1:])


def _parse_in_scope(raw: str, scope: str, rest: tuple[str, ...]) -> BrowsePath:
    """Parse what follows one scope's folder: nothing, a Honey row, or a Cell's wax folder."""
    folder = folder_for_scope(scope)
    # The scope's own folder: its rows.
    if not rest:
        return BrowsePath(kind=PathKind.SCOPE, path=folder, scope=scope)
    # Only a Cell's folder has a `wax` sub-folder; everywhere else a name is a Honey row's id.
    if scope.startswith(CELL_SCOPE_PREFIX) and rest[0] == WAX_FOLDER:
        return _parse_wax(raw, scope, rest[1:])
    # One name under the folder: a Honey row's id.
    if len(rest) == 1:
        path = f"{folder}{_SEPARATOR}{rest[0]}"
        return BrowsePath(kind=PathKind.HONEY, path=path, scope=scope, item_id=rest[0])
    raise BrowsePathError(raw, f"{folder} holds Honey rows directly, with no deeper folders")


def _parse_wax(raw: str, cell_scope: str, rest: tuple[str, ...]) -> BrowsePath:
    """Parse what follows a Cell's `wax` folder: nothing, or one live note."""
    # The wax folder itself: the Cell's live notes.
    if not rest:
        return BrowsePath(
            kind=PathKind.WAX_FOLDER, path=wax_folder_path(cell_scope), scope=cell_scope
        )
    # One note, by its own id.
    if len(rest) == 1:
        path = wax_note_path(cell_scope, rest[0])
        return BrowsePath(kind=PathKind.WAX_NOTE, path=path, scope=cell_scope, item_id=rest[0])
    raise BrowsePathError(raw, "a Cell's wax folder holds notes directly, with no deeper folders")


# Top-level folders with a shape of their own; the three index folders share `_parse_scoped`.
_TOP_PARSERS: dict[str, Callable[[str, tuple[str, ...]], BrowsePath]] = {
    HIVE_FOLDER: _parse_hive,
    BEE_BREAD_FOLDER: _parse_bee_bread,
}
