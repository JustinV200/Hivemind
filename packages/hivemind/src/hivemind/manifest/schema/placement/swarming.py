"""Define ``[virtual_cells.swarming]``: how far the Virtual fleet may scale up (ADR-0063).

Roadmap step 13.3: the manifest's own face of `hivemind.queen.scheduler.SwarmingPolicy`. A
Hive-wide ``max_parallel`` and ``max_cost_per_hour_usd``, and per backend
``[virtual_cells.swarming.backends.<name>]`` with ``max_cells``, ``cost_per_hour_usd`` and
``draws_on_hive_stand``. Every field is unset by default, so a manifest without the section
provisions exactly as before. Prices are the operator's, stated here, so a cost cap holds before
any Cell exists to report its spend.

Fits into the Hive:
    Layer 1 (foundational services; capacity as data). Embedded by
    ``hivemind.manifest.schema.placement.sections.VirtualCellsSection``; converted to the runtime
    policy by ``hivemind.cli.compose.deps.queen``. Calls into pydantic only.

Key invariants:
    - Every model here is frozen and forbids unknown fields (codingrules section 8.5).
    - A backend key is a registry name (``BackendName``), so a misspelt one fails at load.

See Also:
    - docs/adr/0063-swarming-policy-and-cost-caps.md for what each bound means.
    - hivemind.queen.scheduler.swarming for the policy these fields feed.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

__all__ = ["BackendName", "SwarmingBackendSection", "VirtualCellsSwarmingSection"]

# The hivemind.hive.CellBackend registry names a manifest may provision from.
BackendName = Literal["docker", "qemu", "fake"]

_MODEL_CONFIG = ConfigDict(frozen=True, extra="forbid")


class SwarmingBackendSection(BaseModel):
    """``[virtual_cells.swarming.backends.<name>]``: one backend's own Swarming bounds."""

    model_config = _MODEL_CONFIG

    max_cells: int | None = Field(
        default=None,
        gt=0,
        description="The most Cells this backend may hold or be making at once; unset for no cap "
        "beyond [virtual_cells] max_cells.",
    )
    cost_per_hour_usd: float = Field(
        default=0.0,
        ge=0,
        description="What one of its Cells costs an hour while held, in US dollars; 0 (a local "
        "backend) is never cost-capped.",
    )
    draws_on_hive_stand: bool = Field(
        default=False,
        description="Whether its Cells run on the Hive Stand's own host, so the Stand's free "
        "memory and idle cores, measured live against one Cell's reservation, bound how many more "
        "it may start. Opt-in: a reservation is a ceiling, not a use.",
    )


class VirtualCellsSwarmingSection(BaseModel):
    """``[virtual_cells.swarming]``: the Hive-wide bounds, and each backend's own."""

    model_config = _MODEL_CONFIG

    max_parallel: int | None = Field(
        default=None,
        gt=0,
        description="The most Virtual Cells held (dormant ones included) or being made at once, "
        "across every backend; unset for no Hive-wide cap.",
    )
    max_cost_per_hour_usd: float | None = Field(
        default=None,
        ge=0,
        description="The most every Virtual Cell held or being made may cost an hour together, "
        "priced by each backend's cost_per_hour_usd; unset for no cost cap.",
    )
    backends: dict[BackendName, SwarmingBackendSection] = Field(
        default_factory=dict,
        description="Per-backend bounds, keyed by registry name; a backend absent here has none.",
    )
