"""Define CellBackend: how the Hive provisions, destroys, pauses and inspects Virtual Cells.

A Virtual Cell (an isolated VM or container the Hive creates for one Worker, a subagent, and
destroys or Overwinters afterwards) is the owned counterpart of a Real Cell, an existing device
that is borrowed and released instead (see hivemind.cell). The Hive needs to create, inspect,
pause and destroy Virtual Cells on more than one kind of infrastructure: local containers for
development, a local hypervisor, and cloud providers. This module defines the single interface all
of those share so the Queen (the orchestrator) never depends on a specific backend, extending
codingrules Appendix A.1's own `provision`/`destroy` pair with `list_cells` (what a backend's
infrastructure actually holds, read by the Undertaker's orphan sweep and `hive cells abscond`),
`pause`/`resume` (Overwintering, roadmap step 5.9), and a `name` plus `BackendCapabilities` so a
caller branches on what a backend can do, never on which one it is (codingrules section 8.6's
"capabilities are declared, not assumed" applied to Cell backends instead of LLM providers).

Fits into the Hive:
    Layer 3 (sources of Cells). Called by hivemind.hive.lifecycle (roadmap step 5.6, not yet
    built), which the Queen uses through the hive package's public API, and by
    hivemind.hive.registry.BackendRegistry, which looks a backend up by name for the composition
    root. Implementations live in hivemind.hive.backends.*: hivemind.hive.backends.fake now,
    hivemind.hive.backends.docker and .qemu in later steps. The Cell this returns is handed to
    workers.launch (a later phase), which opens an InCellSession on it.

Key invariants:
    - provision() either returns a running Cell or raises CellProvisionError; it never leaves a
      half-created Virtual Cell behind (implementations must clean up on failure).
    - destroy() is idempotent: destroying an already-destroyed or unknown Cell is a no-op, not an
      error.
    - Every Cell returned has kind == CellKind.VIRTUAL and access_level == AccessLevel.FULL
      (Cell's own validator enforces the latter).
    - list_cells(hive_id) returns only Cells that actually exist on the infrastructure right now:
      a failed provision() leaves nothing to list, and a destroyed Cell disappears from it.
    - pause()/resume() raise BackendCapabilityError, never attempt a partial pause, when
      capabilities.can_pause is False: callers branch on capabilities, never on backend name.

See Also:
    - .claude/codingrules.md Appendix A.1, which this module implements almost verbatim, extended
      with list_cells, pause, resume, name and capabilities for roadmap step 5.2.
    - .claude/codingrules.md section 8.6 for the "capabilities, never name" rule this module
      applies to Cell backends.
    - hivemind.cell for the Cell abstraction both Real and Virtual Cells share.
    - hivemind.hive.backends.fake for the reference (in-memory) implementation.
    - hivemind.hive.registry for BackendRegistry, which looks up a CellBackend by name.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Annotated, Protocol

from pydantic import BaseModel, ConfigDict, Field

from hivemind.cell import Cell
from hivemind.hive.cell_state import VirtualCellStatus
from hivemind.hive.models import VirtualCellSpec
from waggle.ids import CellId, HiveId

__all__ = ["BackendCapabilities", "CellBackend", "VirtualCellRecord"]


class BackendCapabilities(BaseModel):
    """What one CellBackend can do, declared up front so callers never branch on its name.

    Mirrors `hivemind.llm.capabilities.ProviderCapabilities`' role for LLM providers (codingrules
    section 8.6's "capabilities are declared, not assumed", applied to Cell backends instead).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    can_snapshot: bool = Field(
        description="Whether this backend can snapshot and roll back a Cell (hivemind.hive."
        "snapshot, roadmap step 5.10); False gets a documented no-op there, never a crash."
    )
    can_pause: bool = Field(
        description="Whether this backend's pause()/resume() actually suspend a Cell rather than "
        "raising BackendCapabilityError (Overwintering, roadmap step 5.9)."
    )
    headroom: Annotated[int, Field(ge=0)] | None = Field(
        default=None,
        description="The most Virtual Cells this backend may hold at once, or None when it "
        "declares no limit of its own (a manifest-level cap may still apply above it).",
    )


@dataclass(frozen=True, slots=True)
class VirtualCellRecord:
    """One Virtual Cell as a backend currently sees it: enough to reconcile without asking twice.

    Returned by `CellBackend.list_cells`, read by the Queen's startup orphan sweep (the
    Undertaker, roadmap step 5.8) and `hive cells abscond` (roadmap step 5.13) -- both of which
    find Cells "from backend labels alone" (codingrules Appendix C: "Backend labels are the
    source of truth; the table is reconciled against them").

    Attributes:
        cell_id: This Cell's id, as minted by the backend at provision.
        status: This Cell's status as far as the backend can tell without asking its Warden --
            READY/DORMANT are inferred from the backend's own resource state, not from a
            Heartbeat, so a caller reconciling this against its own table should prefer its own
            more recent status when the two disagree.
        image: The image this Cell was provisioned from.
        labels: Every label the backend stamped on this Cell's underlying resource, hive_id
            included; how a caller filters or tags without asking the backend's native API twice.
        created_at: When the backend's own resource was created.
    """

    cell_id: CellId
    status: VirtualCellStatus
    image: str
    labels: Mapping[str, str]
    created_at: datetime


class CellBackend(Protocol):
    """Provision, destroy, pause and inspect Virtual Cells on one kind of infrastructure.

    Implementations are registered by name in `hivemind.hive.registry.BackendRegistry`, keyed by
    the name used in the Hive Manifest (`[hive] backend = "docker"`), and constructed in the
    composition root. They must be safe to call concurrently: the Queen may provision several
    Cells at once during Swarming (scale-up).
    """

    @property
    def name(self) -> str:
        """This backend's registry name ("docker", "qemu", "fake", ...)."""
        ...

    @property
    def capabilities(self) -> BackendCapabilities:
        """What this backend can do; callers branch on this, never on `name`."""
        ...

    async def provision(self, spec: VirtualCellSpec) -> Cell:
        """Create a Virtual Cell matching `spec` and return it once it is reachable.

        Args:
            spec: Image, resources, lifetime and network policy for the new Cell. Already
                validated against the manifest; implementations may assume it is well-formed.

        Returns:
            A Cell of kind VIRTUAL whose capabilities reflect the image (a desktop image reports
            has_display and has_audio). The Cell is running, its Warden has connected out to the
            Queen (Virtual Cells expose no inbound ports) and its first Heartbeat has arrived
            when this returns.

        Raises:
            CellProvisionError: The backend could not create the Cell, or it did not become
                reachable within `spec.ready_timeout_s`. Any partial resources are already
                cleaned up when this is raised.
        """
        ...

    async def destroy(self, cell_id: CellId) -> None:
        """Tear down the Virtual Cell and release every resource it held.

        Idempotent: unknown or already-destroyed ids return silently, because an Undertaker
        (the cleanup Worker) may retry after a partial failure.

        Args:
            cell_id: The Cell to destroy.

        Raises:
            CellDestroyError: The backend acknowledged the Cell exists but could not remove it.
                The caller should record this on the Pheromone Trail and retry later.
        """
        ...

    async def list_cells(self, hive_id: HiveId) -> Sequence[VirtualCellRecord]:
        """Return every Virtual Cell this backend currently holds for `hive_id`.

        Args:
            hive_id: Which Hive to list; matched against each Cell's stamped `hive_id` label, not
                against any process-local table.

        Returns:
            One VirtualCellRecord per Cell whose resources actually exist on the infrastructure
            right now, in no particular order. Never includes a Cell a failed provision() cleaned
            up, or one a completed destroy() already removed.
        """
        ...

    async def pause(self, cell_id: CellId) -> None:
        """Suspend `cell_id`, keeping its disk but freeing the compute it held (Overwintering).

        Args:
            cell_id: The Cell to pause.

        Raises:
            BackendCapabilityError: `capabilities.can_pause` is False; the caller should destroy
                the Cell instead of Overwintering it.
        """
        ...

    async def resume(self, cell_id: CellId) -> None:
        """Wake a paused `cell_id` back to a running, reachable Cell.

        Args:
            cell_id: The Cell to resume.

        Raises:
            BackendCapabilityError: `capabilities.can_pause` is False.
        """
        ...
