"""Re-export the ``[placement]`` and ``[virtual_cells]`` sections, Swarming's own included.

Roadmap step 13.3 made this a package: ``[virtual_cells.swarming]`` (ADR-0063) did not fit beside
the rest under codingrules 5.1's file cap. `sections` holds ``[placement]`` and ``[virtual_cells]``
as before; `swarming` holds ``[virtual_cells.swarming]``. Every name the old module exported is
still importable from here.

Fits into the Hive:
    Layer 1 (foundational services; capacity as data). Embedded by
    ``hivemind.manifest.schema.manifest.HiveManifest``.

Key invariants:
    - This module contains no logic of its own: it is a face over `.sections` and `.swarming`
      (codingrules 5.2).

See Also:
    - hivemind.manifest.schema.placement.sections for the placement and Virtual Cell sections.
    - hivemind.manifest.schema.placement.swarming for the Swarming bounds.

Public API:
    - Every name in `sections.__all__`, plus BackendName, SwarmingBackendSection and
      VirtualCellsSwarmingSection (swarming).
"""

from hivemind.manifest.schema.placement.sections import (
    DEFAULT_ALLOW_HIVE_STAND,
    DEFAULT_MAX_CELLS,
    DEFAULT_OVERWINTER_DISK_BUDGET_MB,
    DEFAULT_OVERWINTER_ENABLED,
    DEFAULT_OVERWINTER_MAX_CELLS,
    DEFAULT_OVERWINTER_MAX_DORMANT_S,
    DEFAULT_OVERWINTER_MAX_PER_IMAGE,
    DEFAULT_PREFER,
    DEFAULT_READY_TIMEOUT_S,
    DEFAULT_SNAPSHOT_DISK_BUDGET_MB,
    DEFAULT_SNAPSHOT_RETENTION_S,
    DEFAULT_VIRTUAL_CELLS_CPU_CORES,
    DEFAULT_VIRTUAL_CELLS_DISK_BYTES,
    DEFAULT_VIRTUAL_CELLS_EXOSKELETON_IMAGE,
    DEFAULT_VIRTUAL_CELLS_IMAGE,
    DEFAULT_VIRTUAL_CELLS_LISTEN_HOST,
    DEFAULT_VIRTUAL_CELLS_LISTEN_PORT,
    DEFAULT_VIRTUAL_CELLS_MAX_SUB_BEES,
    DEFAULT_VIRTUAL_CELLS_MEMORY_BYTES,
    DEFAULT_VIRTUAL_CELLS_NETWORK_POLICY,
    MIN_CONTROL_SUBNET_ADDRESSES,
    NetworkPolicyName,
    PlacementRoleOverride,
    PlacementSection,
    PreferName,
    VirtualCellsOverwinterSection,
    VirtualCellsSection,
)
from hivemind.manifest.schema.placement.swarming import (
    BackendName,
    SwarmingBackendSection,
    VirtualCellsSwarmingSection,
)

__all__ = [
    "DEFAULT_ALLOW_HIVE_STAND",
    "DEFAULT_MAX_CELLS",
    "DEFAULT_OVERWINTER_DISK_BUDGET_MB",
    "DEFAULT_OVERWINTER_ENABLED",
    "DEFAULT_OVERWINTER_MAX_CELLS",
    "DEFAULT_OVERWINTER_MAX_DORMANT_S",
    "DEFAULT_OVERWINTER_MAX_PER_IMAGE",
    "DEFAULT_PREFER",
    "DEFAULT_READY_TIMEOUT_S",
    "DEFAULT_SNAPSHOT_DISK_BUDGET_MB",
    "DEFAULT_SNAPSHOT_RETENTION_S",
    "DEFAULT_VIRTUAL_CELLS_CPU_CORES",
    "DEFAULT_VIRTUAL_CELLS_DISK_BYTES",
    "DEFAULT_VIRTUAL_CELLS_EXOSKELETON_IMAGE",
    "DEFAULT_VIRTUAL_CELLS_IMAGE",
    "DEFAULT_VIRTUAL_CELLS_LISTEN_HOST",
    "DEFAULT_VIRTUAL_CELLS_LISTEN_PORT",
    "DEFAULT_VIRTUAL_CELLS_MAX_SUB_BEES",
    "DEFAULT_VIRTUAL_CELLS_MEMORY_BYTES",
    "DEFAULT_VIRTUAL_CELLS_NETWORK_POLICY",
    "MIN_CONTROL_SUBNET_ADDRESSES",
    "BackendName",
    "NetworkPolicyName",
    "PlacementRoleOverride",
    "PlacementSection",
    "PreferName",
    "SwarmingBackendSection",
    "VirtualCellsOverwinterSection",
    "VirtualCellsSection",
    "VirtualCellsSwarmingSection",
]
