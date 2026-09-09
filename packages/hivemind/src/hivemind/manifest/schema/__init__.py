"""Re-export the Hive Manifest schema: every TOML section, as one frozen pydantic model tree.

Fourteen documented sections is more than one module can hold under codingrules section 5.1's
300-line limit, so the schema is a package: ``core`` (identity and lifecycle: ``[hive]``,
``[queen]``, ``[hive_stand]``, ``[brood_chamber]``, ``[pheromone]``), ``llm`` (``[llm]`` and its
provider and slot-binding tables), ``forage`` (``[forage]`` and its role, map and reserve tables),
``supervision`` (``[supervision]`` and ``[memory]``), ``security`` (``[security]`` and
``[honey.clearance]``), and ``manifest`` (``HiveManifest``, the root that gathers all of the
above). This file is the schema's face: a caller imports any section model from here without
knowing which module defines it.

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
      MANIFEST_KEY_PATTERN.
    - Forage (forage): ForageSection.
    - Supervision and memory (supervision): SupervisionSection, MemorySection.
    - Security and clearance (security): SecuritySection, TierProfile, HoneySection,
      HoneyClearanceSection, ClearanceMatrix.
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
from hivemind.manifest.schema.forage import ForageSection
from hivemind.manifest.schema.llm import (
    MANIFEST_KEY_PATTERN,
    CapabilityOverrides,
    LlmSection,
    ProviderKind,
    ProviderSpec,
    SlotBinding,
)
from hivemind.manifest.schema.manifest import HiveManifest
from hivemind.manifest.schema.security import (
    ClearanceMatrix,
    HoneyClearanceSection,
    HoneySection,
    SecuritySection,
    TierProfile,
)
from hivemind.manifest.schema.supervision import MemorySection, SupervisionSection

__all__ = [
    "MANIFEST_KEY_PATTERN",
    "BroodChamberSection",
    "CapabilityOverrides",
    "ClearanceMatrix",
    "ForageSection",
    "HiveManifest",
    "HiveSection",
    "HiveStandCapacityOverrides",
    "HiveStandSection",
    "HoneyClearanceSection",
    "HoneySection",
    "LlmSection",
    "MemorySection",
    "PheromoneSection",
    "ProviderKind",
    "ProviderSpec",
    "QueenSection",
    "SecuritySection",
    "SlotBinding",
    "SupervisionSection",
    "TierProfile",
]
