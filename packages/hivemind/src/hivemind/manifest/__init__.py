"""Load and validate the Hive Manifest: the TOML file that configures one running Hive.

A Hive Manifest describes one Hive (a Queen, its Wardens, and the Cells they supervise): which
model providers it may call, how Forage divides capacity, what its escalation policy and memory
budgets are, and its security posture. Codingrules section 13 fixes the shape: "all configuration
is a Hive Manifest... validated into HiveManifest (pydantic)... environment variables are read in
exactly one place... secrets are never in the manifest file." This package is that whole pipeline:
``schema`` (the fourteen-section model tree, ``HiveManifest`` at its root), ``loader``
(``load_manifest``, TOML file to validated model), ``env`` (``read_env``/``apply_env``/
``provider_api_key``, the one place `HIVEMIND_*` is read), and ``errors`` (``ManifestError``, the
one exception every failure in this package raises).

Fits into the Hive:
    Layer 1 (foundational services; capacity as data). Called by the composition root
    (`cli/stores.py`, a later roadmap step) to build the one `HiveManifest` a running Hive is
    configured from, and by every subsystem above Layer 1 for its own slice of it. Calls into
    `hivemind.forage`, `hivemind.common` and `waggle` only (codingrules section 4: `manifest` and
    `hivemind.llm`/`hivemind.cell` are independent packages that may not import each other).

Key invariants:
    - Nothing outside `hivemind.manifest.env` ever reads a `HIVEMIND_*` environment variable
      (codingrules section 13).
    - No field named `api_key` exists anywhere in the schema; a provider's secret always comes
      from an environment variable, read by `provider_api_key` and held in a `SecretStr`.
    - Every example manifest under `docs/manifests/` loads through `load_manifest` in a test, so
      the shipped docs cannot drift from the schema (codingrules section 13).

See Also:
    - .claude/codingrules.md section 13 for the configuration-and-secrets rules this package is.
    - .claude/roadmap.md step 3.1 for the field-by-field description this package implements.
    - docs/manifests/README.md for the example manifests and the `HIVEMIND_*` variable table.
    - hivemind.forage for ModelSlot, RoleFootprint, ModelSourceSpec and RoyalReserve, embedded
      directly in this package's schema.

Public API:
    - Schema (`hivemind.manifest.schema`): HiveManifest and every section model -- HiveSection,
      QueenSection, HiveStandSection, HiveStandCapacityOverrides, BroodChamberSection,
      PheromoneSection, LlmSection, ProviderKind, ProviderSpec, CapabilityOverrides, SlotBinding,
      MANIFEST_KEY_PATTERN, ForageSection, SupervisionSection, MemorySection, SecuritySection,
      TierProfile, HoneySection, HoneyClearanceSection, ClearanceMatrix.
    - Loading (`hivemind.manifest.loader`): load_manifest.
    - Environment (`hivemind.manifest.env`): EnvOverrides, read_env, apply_env, provider_api_key.
    - Errors (`hivemind.manifest.errors`): ManifestError.
"""

from hivemind.manifest.env import EnvOverrides, apply_env, provider_api_key, read_env
from hivemind.manifest.errors import ManifestError
from hivemind.manifest.loader import load_manifest
from hivemind.manifest.schema import (
    MANIFEST_KEY_PATTERN,
    BroodChamberSection,
    CapabilityOverrides,
    ClearanceMatrix,
    ForageSection,
    HiveManifest,
    HiveSection,
    HiveStandCapacityOverrides,
    HiveStandSection,
    HoneyClearanceSection,
    HoneySection,
    LlmSection,
    MemorySection,
    PheromoneSection,
    ProviderKind,
    ProviderSpec,
    QueenSection,
    SecuritySection,
    SlotBinding,
    SupervisionSection,
    TierProfile,
)

__all__ = [
    "MANIFEST_KEY_PATTERN",
    "BroodChamberSection",
    "CapabilityOverrides",
    "ClearanceMatrix",
    "EnvOverrides",
    "ForageSection",
    "HiveManifest",
    "HiveSection",
    "HiveStandCapacityOverrides",
    "HiveStandSection",
    "HoneyClearanceSection",
    "HoneySection",
    "LlmSection",
    "ManifestError",
    "MemorySection",
    "PheromoneSection",
    "ProviderKind",
    "ProviderSpec",
    "QueenSection",
    "SecuritySection",
    "SlotBinding",
    "SupervisionSection",
    "TierProfile",
    "apply_env",
    "load_manifest",
    "provider_api_key",
    "read_env",
]
