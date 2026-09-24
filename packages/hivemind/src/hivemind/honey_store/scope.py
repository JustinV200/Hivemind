"""Derive a Honey row's scope from provenance, and the folders and capabilities built around it.

A scope names where a Honey (ripened knowledge) or Nectar (raw finding) row is filed: `hive`
(shared knowledge every reader may ask for), `cell:<id>` (one Cell's own history), `bee:<id>` (one
bee's own material) or `task:<id>` (one task's working material). `scope_for_nectar` is ADR-0031's
scoping table made code; `folder_for_scope`/`scope_for_folder` are its inverse, the browser path
(roadmap 7.10) every scope maps to one-to-one; `honey_ref`/`parse_honey_ref` build and read a
Honey row's public reference, which is also `waggle.messages.honey.hit.HoneyHit.honey_ref`. The
`queen_read_capabilities`/`warden_read_capabilities`/`worker_read_capabilities` builders are
ADR-0031's stated defaults until phase 10's policy engine issues real capability sets;
`readable_globs`/`is_readable` are the read side, built on `hivemind.guard`'s existing glob
matching so this module adds no matching logic of its own.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy), inside the honey_store package. Called
    by `hivemind.honey_store.nectar` (intake, a later dispatch, for `scope_for_nectar`) and by
    whatever assembles a principal's default capabilities before it queries Honey (the Queen, a
    Warden, a Worker). Calls into `hivemind.guard` (Capability, CapabilityFamily, CapabilitySet),
    `hivemind.honey_store.models.nectar` (NectarOrigin) and `hivemind.honey_store.errors`
    (InvalidScopeError) and `waggle` only.

Key invariants:
    - Every scope this module builds or accepts matches
      `waggle.messages.honey.hit.SCOPE_PATTERN` and, stricter, an id of letters, digits, `_` and
      `-` only (no `/`, no GLOB metacharacter); a mismatch always raises `InvalidScopeError`,
      never a silently-accepted new scope kind.
    - `folder_for_scope` and `scope_for_folder` are exact inverses for every scope this module can
      build (`tests/unit/honey_store/test_scope.py` round-trips every kind both ways).
    - `is_readable`/`readable_globs` never implement their own glob rule: both call into
      `hivemind.guard.Capability`/`CapabilitySet`, so scope matching and ordinary capability
      matching can never drift apart.

See Also:
    - docs/adr/0031-honey-store-sqlite-fts5-sqlite-vec.md for the scoping table this implements.
    - docs/waggle/spec.md section 8.7 for SCOPE_PATTERN and the four scope kinds.
    - hivemind.guard.capabilities for CapabilityFamily.HONEY_READ, the glob family this reuses.
    - hivemind.honey_store.models.nectar for NectarOrigin.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from hivemind.guard import Capability, CapabilityFamily, CapabilitySet
from hivemind.honey_store.errors import InvalidScopeError
from hivemind.honey_store.models.nectar import NectarOrigin
from waggle.ids import CellId, TaskId, WardenId, WorkerId
from waggle.messages.honey import NectarKind
from waggle.messages.honey.hit import SCOPE_PATTERN

HIVE_SCOPE = "hive"  # Shared knowledge every reader with any honey:read capability may ask for.

_WIRE_SCOPE_RE = re.compile(SCOPE_PATTERN)  # The wire's own shape (docs/waggle/spec.md 8.7).
# Stricter than the wire: an id is letters, digits, '_' and '-' only, so a stored scope can never
# carry a '/' (a deeper browser path) or a GLOB metacharacter ('*', '?', '[') that would turn the
# store's exact `honey:read` match into a pattern.
_SCOPE_RE = re.compile(r"^(hive|(cell|bee|task):[A-Za-z0-9_-]+)$")
_HIVE_FOLDER = "/hive"
_CELL_FOLDER_PREFIX = "/cells/"
_BEE_FOLDER_PREFIX = "/bees/"
_TASK_FOLDER_PREFIX = "/tasks/"
# Scope kind -> browser folder prefix, in the one order scope_for_folder walks; the dict below is
# derived from this same tuple, so folder_for_scope's lookup can never drift out of sync with it.
_FOLDER_PREFIXES: tuple[tuple[str, str], ...] = (
    ("cell", _CELL_FOLDER_PREFIX),
    ("bee", _BEE_FOLDER_PREFIX),
    ("task", _TASK_FOLDER_PREFIX),
)
_FOLDER_PREFIX_BY_KIND: dict[str, str] = dict(_FOLDER_PREFIXES)

__all__ = [
    "HIVE_SCOPE",
    "NectarProvenance",
    "bee_scope",
    "cell_scope",
    "folder_for_scope",
    "honey_ref",
    "is_readable",
    "parse_honey_ref",
    "queen_read_capabilities",
    "readable_globs",
    "scope_for_folder",
    "scope_for_nectar",
    "task_scope",
    "warden_read_capabilities",
    "worker_read_capabilities",
]


def cell_scope(cell_id: CellId) -> str:
    """Build the scope string for one Cell's own history.

    Args:
        cell_id: The Cell.

    Returns:
        `"cell:<cell_id>"`.
    """
    return _validated(f"cell:{cell_id}")


def task_scope(task_id: TaskId) -> str:
    """Build the scope string for one task's working material.

    Args:
        task_id: The task (or goal: a goal's id is its first task's `TaskId`).

    Returns:
        `"task:<task_id>"`.
    """
    return _validated(f"task:{task_id}")


def bee_scope(bee: WorkerId | WardenId) -> str:
    """Build the scope string for one bee's own material.

    Args:
        bee: The Worker or Warden.

    Returns:
        `"bee:<bee>"`.
    """
    return _validated(f"bee:{bee}")


def folder_for_scope(scope: str) -> str:
    """Map a scope string to its browser folder (roadmap 7.10).

    Args:
        scope: A scope matching `SCOPE_PATTERN`.

    Returns:
        `"/hive"`, `"/cells/<id>"`, `"/bees/<id>"` or `"/tasks/<id>"`.

    Raises:
        InvalidScopeError: `scope` does not match `SCOPE_PATTERN`.
    """
    _validated(scope)
    if scope == HIVE_SCOPE:
        return _HIVE_FOLDER
    kind, _, ident = scope.partition(":")
    return f"{_FOLDER_PREFIX_BY_KIND[kind]}{ident}"


def scope_for_folder(path: str) -> str | None:
    """Map a browser folder back to its scope string; the inverse of `folder_for_scope`.

    Args:
        path: A folder path, e.g. `"/cells/cell_01H..."`.

    Returns:
        The matching scope, or None when `path` names no scope this module builds.
    """
    if path == _HIVE_FOLDER:
        return HIVE_SCOPE
    # Every other folder kind shares one shape: a fixed prefix followed by a non-empty id. The
    # result must itself be a valid scope, so a deeper path (a Cell's live wax folder,
    # "/cells/<id>/wax") is never mistaken for the Cell's own scope with junk appended.
    for kind, prefix in _FOLDER_PREFIXES:
        if path.startswith(prefix) and len(path) > len(prefix):
            scope = f"{kind}:{path[len(prefix) :]}"
            return scope if _SCOPE_RE.fullmatch(scope) else None
    return None


def honey_ref(scope: str, honey_id: str) -> str:
    """Build a Honey row's public reference: its browser path and `HoneyHit.honey_ref`.

    Args:
        scope: The row's scope.
        honey_id: The row's own id.

    Returns:
        `"<folder>/<honey_id>"`.
    """
    return f"{folder_for_scope(scope)}/{honey_id}"


def parse_honey_ref(ref: str) -> tuple[str, str]:
    """Split a Honey reference back into its scope and honey id; the inverse of `honey_ref`.

    Args:
        ref: A reference built by `honey_ref`.

    Returns:
        `(scope, honey_id)`.

    Raises:
        InvalidScopeError: `ref`'s folder half names no scope this module builds.
    """
    folder, _, honey_id = ref.rpartition("/")
    scope = scope_for_folder(folder)
    if scope is None:
        raise InvalidScopeError(ref)
    return scope, honey_id


@dataclass(frozen=True, slots=True)
class NectarProvenance:
    """The raw provenance `scope_for_nectar` (and only that function) derives a scope from.

    codingrules 5.1: a function's argument group past four values becomes a dataclass;
    ADR-0031's scoping table reads five of these fields at once, so they travel together here
    rather than as five loose parameters.
    """

    kind: NectarKind  # What sort of finding this is; decides scope only for a BEE-origin deposit.
    origin: NectarOrigin  # How the deposit reached the store; decides scope for every other origin.
    task_id: TaskId | None  # The task it came from, if any.
    cell_id: CellId  # The Cell it was gathered on.
    bee: WorkerId | WardenId | None  # The Worker or Warden that gathered it, if any.


def scope_for_nectar(provenance: NectarProvenance, proposed_scope: str | None) -> str:
    """Derive a Nectar deposit's scope from its provenance (ADR-0031's scoping table).

    Args:
        provenance: What sort of finding this is and how it reached the store, plus the task,
            Cell and bee it came from.
        proposed_scope: The human's own proposed scope (HUMAN origin only); `hive` when None.

    Returns:
        A scope matching `SCOPE_PATTERN`.

    Raises:
        InvalidScopeError: `proposed_scope` (HUMAN origin) does not match `SCOPE_PATTERN`.
    """
    origin = provenance.origin
    if origin is NectarOrigin.HUMAN:
        # The human's own proposed folder wins outright; hive when nothing was proposed.
        return _validated(proposed_scope) if proposed_scope is not None else HIVE_SCOPE
    if origin in (NectarOrigin.WATCH, NectarOrigin.CELL_WAX):
        # Watch observations and Cell Wax history are always about one Cell.
        return cell_scope(provenance.cell_id)
    if origin is NectarOrigin.BEE_BREAD:
        # Ripened warm-tier material keeps its task's working scope, else joins shared knowledge.
        return task_scope(provenance.task_id) if provenance.task_id is not None else HIVE_SCOPE
    if origin is NectarOrigin.TASK_OUTCOME:
        return HIVE_SCOPE  # A verified task outcome is shared knowledge.
    return _scope_for_bee_kind(provenance)  # origin is BEE.


def queen_read_capabilities() -> CapabilitySet:
    """Build the Queen's default Honey read capabilities: everything (ADR-0031).

    Returns:
        A CapabilitySet holding a single `honey:read:*` capability.
    """
    return CapabilitySet.parse(f"{CapabilityFamily.HONEY_READ.value}:*")


def warden_read_capabilities(cell_id: CellId, warden_id: WardenId) -> CapabilitySet:
    """Build a Warden's default Honey read capabilities: hive, its Cell, itself (ADR-0031).

    Args:
        cell_id: The Cell this Warden supervises.
        warden_id: The Warden itself.

    Returns:
        A CapabilitySet with one `honey:read` capability per readable scope.
    """
    return CapabilitySet.parse(
        _honey_read(HIVE_SCOPE), _honey_read(cell_scope(cell_id)), _honey_read(bee_scope(warden_id))
    )


def worker_read_capabilities(
    task_id: TaskId, goal_id: TaskId, cell_id: CellId, bee_id: WorkerId
) -> CapabilitySet:
    """Build a Worker's default Honey read capabilities: hive, its Cell, task, goal, itself.

    Args:
        task_id: The task this Worker is running.
        goal_id: The goal `task_id` belongs to (a goal's id is its first task's TaskId).
        cell_id: The Cell this Worker is running on.
        bee_id: The Worker itself.

    Returns:
        A CapabilitySet with one `honey:read` capability per readable scope; a single-task goal
        collapses `task_id`/`goal_id` into one capability, since CapabilitySet is a set.
    """
    return CapabilitySet.parse(
        _honey_read(HIVE_SCOPE),
        _honey_read(cell_scope(cell_id)),
        _honey_read(task_scope(task_id)),
        _honey_read(task_scope(goal_id)),
        _honey_read(bee_scope(bee_id)),
    )


def readable_globs(caps: CapabilitySet) -> tuple[str, ...]:
    """Return every scope glob a `honey:read` capability in `caps` grants.

    Args:
        caps: The principal's capability set.

    Returns:
        The `honey:read` scopes in `caps`, sorted for a deterministic `ReadFilter.readable`.
    """
    return tuple(
        sorted(
            capability.scope
            for capability in caps
            if capability.family is CapabilityFamily.HONEY_READ
        )
    )


def is_readable(scope: str, caps: CapabilitySet) -> bool:
    """Decide whether `caps` grants read access to `scope`.

    Args:
        scope: The scope a row carries.
        caps: The principal's capability set.

    Returns:
        True when some `honey:read` capability in `caps` globs `scope`.
    """
    return caps.allows(Capability(family=CapabilityFamily.HONEY_READ, scope=scope))


def _honey_read(scope: str) -> str:
    """Build the `"honey:read:<scope>"` capability string for one scope."""
    return f"{CapabilityFamily.HONEY_READ.value}:{scope}"


def _scope_for_bee_kind(provenance: NectarProvenance) -> str:
    """Scope a BEE-origin deposit by its NectarKind (ADR-0031's second and third bullets)."""
    kind = provenance.kind
    if kind in (NectarKind.FINDING, NectarKind.AUDIT_FINDING, NectarKind.RIPENED_HONEY):
        return HIVE_SCOPE  # Shared knowledge: a finding, an audit finding, or ripened Honey.
    if kind is NectarKind.PATROL_SUMMARY:
        return cell_scope(provenance.cell_id)  # A Patrol reviews one Cell.
    # Working material (TRANSCRIPT, TOOL_RESULT, HANDOFF, FLIGHT_RECORDING): stays with the task
    # that produced it, else the bee that produced it, else the Cell it happened on.
    if provenance.task_id is not None:
        return task_scope(provenance.task_id)
    if provenance.bee is not None:
        return bee_scope(provenance.bee)
    return cell_scope(provenance.cell_id)


def _validated(scope: str) -> str:
    """Return `scope` unchanged once it matches both patterns, else raise InvalidScopeError."""
    if not (_WIRE_SCOPE_RE.fullmatch(scope) and _SCOPE_RE.fullmatch(scope)):
        raise InvalidScopeError(scope)
    return scope
