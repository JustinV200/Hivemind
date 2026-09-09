"""Build the three requests a Warden sends the Queen when it needs something beyond its grant.

A Warden never provisions Cells and never asks for shared Forage within its own standing grant
(codingrules section 8.8: "Wardens never provision... a Warden spawns sub-bees within its grant and
requests Cells, more Forage, or tools from the Queen with a reason"). This module is the one place
those three waggle requests are built, each a pure function over what the caller already knows plus
a `reason` string for the trail: `forage_request` is what `hivemind.wardens.ticks.alarms` sends when
a REBIND needs a binding outside the current grant's `allowed_bindings`; `cell_request` is what a
Warden sends when its own lease is refused twice (`AlarmKind.CELL_UNREACHABLE`) and it needs a
fresh Cell; `tool_request` is what a Warden forwards on behalf of a sub-bee that asked for a
capability it lacks. None of the three is answered synchronously in v0: the reply (or its absence)
is the Queen's concern, a later roadmap step. Each request's own varying fields are grouped into a
frozen dataclass first (codingrules section 5.1: "introduce a frozen dataclass for the argument
group"), since every one of the three waggle messages carries more fields than that section's own
five-parameter limit allows on one function signature.

Fits into the Hive:
    Layer 5 (per-Cell supervisors; spawn and supervise Workers), inside the wardens package. Called
    by `hivemind.wardens.ticks.alarms` (forage_request, on an unreachable REBIND) and, in a later
    roadmap step, by the Warden's own Cell-loss and tool-request handling. Calls into
    `waggle.messages.cell`, `waggle.messages.forage`, `waggle.messages.tool` and `waggle.messages.
    reports` only.

Key invariants:
    - Every function here is pure: it validates and returns a waggle message, with no I/O, no
      clock read and no trail write of its own -- the caller sends it and records the trail event.
    - `cell_request` always builds the "any Cell that fits `needs`" form (`cell_id=None`), never
      the "open a lease on exactly this Cell" form: only the Queen names a specific Cell to a
      gateway or another Warden (waggle.messages.cell.leases.CellRequest's own docstring).

See Also:
    - .claude/codingrules.md section 5.1 for the parameter-count limit the three input dataclasses
      exist to keep.
    - .claude/codingrules.md section 8.8 for "Wardens never provision" and the three request kinds.
    - waggle.messages.forage for ForageRequest, ForageDelta and ForageRequestKind.
    - waggle.messages.cell for CellRequest and waggle.messages.cell.status.TaskNeedsReport.
    - waggle.messages.tool for ToolRequest, ToolScope and waggle.messages.reports.PlatformReport.
"""

from __future__ import annotations

from dataclasses import dataclass

from waggle.ids import CellId, GrantId, TaskId, ToolId, WardenId, WorkerId
from waggle.messages.cell import CellRequest
from waggle.messages.cell.status import TaskNeedsReport
from waggle.messages.forage import ForageDelta, ForageRequest, ForageRequestKind
from waggle.messages.labels import AccessLevel, Tempo
from waggle.messages.reports import PlatformReport
from waggle.messages.tool import ToolRequest, ToolScope

__all__ = [
    "CellRequestInputs",
    "ForageRequestInputs",
    "ToolRequestInputs",
    "cell_request",
    "forage_request",
    "tool_request",
]


@dataclass(frozen=True, slots=True)
class ForageRequestInputs:
    """Everything `forage_request` needs beyond its own `reason`."""

    grant_id: GrantId  # The grant the Warden wants extended.
    kind: ForageRequestKind  # Which dimension is asked for.
    wanted: ForageDelta  # The delta wanted; at least one non-zero field.
    task_id: TaskId | None  # The task the extra Forage serves; None for the Warden's own needs.
    tempo: Tempo  # The requesting task's Tempo, an allocator input at the Queen.


def forage_request(inputs: ForageRequestInputs, reason: str) -> ForageRequest:
    """Build a request for shared Forage beyond the Warden's standing grant.

    Args:
        inputs: The grant, dimension, delta, task and Tempo this request carries.
        reason: Why the Warden needs it, for the trail.

    Returns:
        A validated ForageRequest, ready to send to the Queen.
    """
    return ForageRequest(
        grant_id=inputs.grant_id,
        kind=inputs.kind,
        wanted=inputs.wanted,
        task_id=inputs.task_id,
        tempo=inputs.tempo,
        reason=reason,
    )


@dataclass(frozen=True, slots=True)
class CellRequestInputs:
    """Everything `cell_request` needs beyond its own `reason`."""

    needs: TaskNeedsReport  # What the work needs; placement decides which Cell (if any) fits.
    holder: WardenId  # This Warden's own id; the resulting lease's holder.
    task_id: TaskId | None  # The task the Cell is for; None for internal use.
    access_level: AccessLevel  # The most this Warden needs on the granted Cell.
    lifetime_s: float | None  # Expected tenancy length, in seconds; None means until released.


def cell_request(inputs: CellRequestInputs, reason: str) -> CellRequest:
    """Build a request for any Cell that fits `inputs.needs`, for this Warden to hold.

    Args:
        inputs: What the work needs, this Warden's own id, the task, access level and lifetime.
        reason: Why the Cell is needed, for the trail.

    Returns:
        A validated CellRequest in its "any Cell that fits" form.
    """
    return CellRequest(
        cell_id=None,
        needs=inputs.needs,
        holder=inputs.holder,
        task_id=inputs.task_id,
        access_level=inputs.access_level,
        lifetime_s=inputs.lifetime_s,
        reason=reason,
    )


@dataclass(frozen=True, slots=True)
class ToolRequestInputs:
    """Everything `tool_request` needs beyond its own `reason`."""

    tool_id: ToolId  # Minted by the first requester; names the tool through every later stage.
    warden_id: WardenId  # This Warden's own id.
    worker_id: WorkerId | None  # The sub-bee whose task needs it, if a sub-bee started the ask.
    task_id: TaskId | None  # The task blocked on the missing capability.
    cell_id: CellId  # The Cell the tool must run on.
    scope: ToolScope  # Requested visibility (HIVE or CELL).
    name: str  # Desired tool name, the capability string tool:<name> once promoted.
    description: str  # What the tool must do, the scaffolder's brief.
    required_capabilities: tuple[str, ...]  # Capability strings the tool will need.
    platform: PlatformReport  # The target Cell's platform.


def tool_request(inputs: ToolRequestInputs, reason: str) -> ToolRequest:
    """Build a request for a capability a sub-bee (or the Warden itself) lacks.

    Args:
        inputs: The tool's id, this Warden's own id, the asking sub-bee (if any), the task, the
            Cell, the requested scope, name, description, needed capabilities and target platform.
        reason: Why the capability is needed now, for the trail.

    Returns:
        A validated ToolRequest, ready to send to the Queen.
    """
    return ToolRequest(
        tool_id=inputs.tool_id,
        warden_id=inputs.warden_id,
        worker_id=inputs.worker_id,
        task_id=inputs.task_id,
        cell_id=inputs.cell_id,
        scope=inputs.scope,
        name=inputs.name,
        description=inputs.description,
        required_capabilities=inputs.required_capabilities,
        platform=inputs.platform,
        reason=reason,
    )
