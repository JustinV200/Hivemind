"""Re-export the Hive Manifest schema: every TOML section, as one frozen pydantic model tree.

Eighteen documented sections is more than one module can hold under codingrules section 5.1's
300-line limit, so the schema is a package: ``core`` (identity and lifecycle: ``[hive]``,
``[queen]``, ``[hive_stand]``, ``[brood_chamber]``, ``[pheromone]``), ``llm`` (``[llm]`` and its
provider and slot-binding tables), ``forage`` (``[forage]`` and its role, map and reserve tables),
``supervision`` (``[supervision]``, with its ``[supervision.reconnect_budget]`` and
``[supervision.watch]``, and ``[memory]``), ``swarm`` (``[swarm]`` with ``[swarm.ladder]`` and
``[swarm.nuc]``, phase 11), ``security`` (a sub-package: ``tiers`` for
``[security]`` and ``[honey.clearance]``, ``guard`` for ``[guard]``, the Guard policy's overrides,
roadmap step 10.2), ``honey`` (``[honey.store]``, ``[honey.ripening]`` and ``[honey.retrieval]``,
roadmap phase 7), ``placement`` (``[placement]`` and ``[virtual_cells]``, roadmap step 5.7),
``exoskeleton`` (``[exoskeleton]``, roadmap step 6.6), ``entrance`` (``[entrance]``, phase 10),
``royal_jelly`` (``[royal_jelly]``, ``[royal_jelly.limits]`` and ``[basket]``, phase 9), and
``manifest`` (``HiveManifest``, the root that gathers all of the above). This file is the schema's
face: a caller imports any section model from here without knowing which module defines it.

Fits into the Hive:
    Layer 1 (foundational services; capacity as data). Imported by ``hivemind.manifest.loader``
    and by ``hivemind.manifest``'s own ``__init__.py``. Calls into every module in this package.

Key invariants:
    - Every class the package defines is re-exported here (tests check this against __all__).
    - This file holds re-exports and __all__ only; no model or validator is defined here
      (codingrules section 5.4).

See Also:
    - .claude/roadmap.md step 3.1 for the full section list this package implements.
    - .claude/codingrules.md section 5.2 for "a concept that needs a second file becomes a package".
    - hivemind.manifest.loader for load_manifest, which validates a TOML document into HiveManifest.

Public API:
    - Identity and lifecycle (core): HiveSection, QueenSection, HiveStandSection,
      HiveStandCapacityOverrides, BroodChamberSection, PheromoneSection.
    - LLM (llm): LlmSection, ProviderKind, ProviderSpec, CapabilityOverrides, SlotBinding,
      ServerSpec, MANIFEST_KEY_PATTERN.
    - Forage (forage): ForageSection.
    - Guard policy overrides (security.guard): GuardSection, GuardRoleSection,
      DEFAULT_DIRE_PATTERNS.
    - Hive Entrance (entrance): EntranceSection, EntranceExposure, EntranceTlsSection,
      EntrancePushSection, EntranceVoiceSection, EntranceAndroidSection.
    - Supervision and memory (supervision): SupervisionSection, ReconnectBudgetSection,
      WatchSection, WatchTuning, MemorySection.
    - The Swarm (swarm): SwarmSection, SwarmLadderSection, SwarmNucSection.
    - Security and clearance (security.tiers): SecuritySection, TierProfile, HoneySection,
      HoneyClearanceSection, ClearanceMatrix.
    - The Honey Store (honey): HoneyStoreSection, HoneyRipeningSection, HoneyRetrievalSection,
      HoneyLoweringSection.
    - Placement and Virtual Cells (placement): PlacementSection, PlacementRoleOverride,
      VirtualCellsSection, VirtualCellsOverwinterSection.
    - Exoskeleton (exoskeleton): ExoskeletonSection.
    - The Nurse Bee (nurse): NurseSection.
    - Royal Jelly Lab and Basket (royal_jelly): RoyalJellySection, QuarantineLimitsSection,
      SandboxKind, BasketSection.
    - Root (manifest): HiveManifest.
"""

from hivemind.manifest.schema.core import (
    BroodChamberSection,
    HiveSection,
    HiveStandCapacityOverrides,
    HiveStandSection,
    PheromoneSection,
    QueenSection,
)
from hivemind.manifest.schema.entrance import (
    EntranceAndroidSection,
    EntranceExposure,
    EntrancePushSection,
    EntranceSection,
    EntranceTlsSection,
    EntranceVoiceSection,
)
from hivemind.manifest.schema.exoskeleton import ExoskeletonSection
from hivemind.manifest.schema.forage import ForageSection
from hivemind.manifest.schema.honey import (
    HoneyLoweringSection,
    HoneyRetrievalSection,
    HoneyRipeningSection,
    HoneyStoreSection,
)
from hivemind.manifest.schema.llm import (
    MANIFEST_KEY_PATTERN,
    CapabilityOverrides,
    LlmSection,
    ProviderKind,
    ProviderSpec,
    ServerSpec,
    SlotBinding,
)
from hivemind.manifest.schema.manifest import HiveManifest
from hivemind.manifest.schema.nurse import NurseSection
from hivemind.manifest.schema.placement import (
    PlacementRoleOverride,
    PlacementSection,
    VirtualCellsOverwinterSection,
    VirtualCellsSection,
)
from hivemind.manifest.schema.royal_jelly import (
    BasketSection,
    QuarantineLimitsSection,
    RoyalJellySection,
    SandboxKind,
)
from hivemind.manifest.schema.security.guard import (
    DEFAULT_DIRE_PATTERNS,
    GuardRoleSection,
    GuardSection,
)
from hivemind.manifest.schema.security.tiers import (
    ClearanceMatrix,
    HoneyClearanceSection,
    HoneySection,
    SecuritySection,
    TierProfile,
)
from hivemind.manifest.schema.supervision import (
    MemorySection,
    ReconnectBudgetSection,
    SupervisionSection,
    WatchSection,
    WatchTuning,
)
from hivemind.manifest.schema.swarm import SwarmLadderSection, SwarmNucSection, SwarmSection

__all__ = [
    "DEFAULT_DIRE_PATTERNS",
    "MANIFEST_KEY_PATTERN",
    "BasketSection",
    "BroodChamberSection",
    "CapabilityOverrides",
    "ClearanceMatrix",
    "EntranceAndroidSection",
    "EntranceExposure",
    "EntrancePushSection",
    "EntranceSection",
    "EntranceTlsSection",
    "EntranceVoiceSection",
    "ExoskeletonSection",
    "ForageSection",
    "GuardRoleSection",
    "GuardSection",
    "HiveManifest",
    "HiveSection",
    "HiveStandCapacityOverrides",
    "HiveStandSection",
    "HoneyClearanceSection",
    "HoneyLoweringSection",
    "HoneyRetrievalSection",
    "HoneyRipeningSection",
    "HoneySection",
    "HoneyStoreSection",
    "LlmSection",
    "MemorySection",
    "NurseSection",
    "PheromoneSection",
    "PlacementRoleOverride",
    "PlacementSection",
    "ProviderKind",
    "ProviderSpec",
    "QuarantineLimitsSection",
    "QueenSection",
    "ReconnectBudgetSection",
    "RoyalJellySection",
    "SandboxKind",
    "SecuritySection",
    "ServerSpec",
    "SlotBinding",
    "SupervisionSection",
    "SwarmLadderSection",
    "SwarmNucSection",
    "SwarmSection",
    "TierProfile",
    "VirtualCellsOverwinterSection",
    "VirtualCellsSection",
    "WatchSection",
    "WatchTuning",
]
