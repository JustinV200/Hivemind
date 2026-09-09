"""Define HiveManifest: the root model every Hive Manifest TOML document validates into.

``HiveManifest`` gathers every section (``[hive]``, ``[queen]``, ``[hive_stand]``, ``[llm]``,
``[forage]``, ``[supervision]``, ``[memory]``, ``[security]``, ``[honey.clearance]``,
``[brood_chamber]``, ``[pheromone]``) into one frozen value. Every section but ``[hive]`` has a
``default_factory``, so a manifest may omit ``[queen]``, ``[hive_stand]``, ``[supervision]``,
``[memory]``, ``[security]``, ``[honey.clearance]``, ``[brood_chamber]`` and ``[pheromone]``
entirely and still validate; ``[hive]`` alone has no default, because an id and a node id are
never guessed on a Hive's behalf (``hivemind.manifest.schema.core.HiveSection``).
``[llm]`` and ``[forage]`` also default to an empty table structurally, but each carries its own
content requirement an empty table cannot satisfy (every ``ModelSlot`` bound; the ``drone`` role
footprint present), so a real "minimal" manifest -- ``docs/manifests/minimal.toml`` -- still
supplies both in full; omitting either raises ``ManifestError`` rather than silently falling back
to nothing bound. This module also carries the two things a section model cannot:
``source_path`` (set by ``hivemind.manifest.loader.load_manifest`` once the file is
known, so ``resolve_path`` can turn a manifest-relative path into an absolute one) and the one
cross-section validator the roadmap calls for that no single section can run on its own -- every
``[forage.map.<source_id>]`` entry's provider must be declared in ``[llm.providers]`` -- because
``ForageSection`` has no visibility into ``LlmSection`` and vice versa (codingrules section 4: they
sit in different, independent Layer-1 packages' worth of schema, joined only here).

Fits into the Hive:
    Layer 1 (foundational services; capacity as data). Built by ``hivemind.manifest.loader.
    load_manifest`` from a parsed TOML document; read by every layer above Layer 1 for its own
    slice (``manifest.llm``, ``manifest.forage``, and so on), never the raw dict that produced it
    (codingrules section 9: "pydantic models are the only thing that reads JSON/TOML"). Calls into
    every other module in this package.

Key invariants:
    - HiveManifest itself is frozen and forbids unknown top-level sections.
    - `source_path` is `None` until `hivemind.manifest.loader.load_manifest` sets it; a manifest
      built directly (a test, or `HiveManifest(hive=...)`) has no source file and `resolve_path`
      falls back to resolving against the current working directory instead.
    - Every `[forage.map.<source_id>].provider` names a key in `llm.providers`; nothing else about
      the two sections is cross-checked (a slot's model need not appear anywhere on the map: the
      map and the slot table are two independent ways of naming what a call may use).

See Also:
    - .claude/roadmap.md step 3.1 for the full section list this module gathers.
    - .claude/codingrules.md section 9 for "pydantic models are the only thing that reads TOML".
    - .claude/codingrules.md section 13 for "paths are resolved relative to the manifest's own
      directory".
    - hivemind.manifest.loader for load_manifest, the only place source_path is set from a file.
    - hivemind.manifest.schema.forage for the ForageSection whose map this module cross-checks.
    - hivemind.manifest.schema.llm for the LlmSection whose providers this module cross-checks.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, model_validator

from hivemind.manifest.schema.core import (
    BroodChamberSection,
    HiveSection,
    HiveStandSection,
    PheromoneSection,
    QueenSection,
)
from hivemind.manifest.schema.forage import ForageSection
from hivemind.manifest.schema.llm import LlmSection
from hivemind.manifest.schema.security import HoneySection, SecuritySection
from hivemind.manifest.schema.supervision import MemorySection, SupervisionSection

__all__ = ["HiveManifest"]

# A frozen, extras-forbidding config matching every section model (codingrules section 8.5).
_MODEL_CONFIG = ConfigDict(frozen=True, extra="forbid")


class HiveManifest(BaseModel):
    """The whole Hive Manifest: every section, with a default for every one but `hive`."""

    model_config = _MODEL_CONFIG

    hive: HiveSection = Field(
        description="The Hive's own identity; the only section with no default."
    )
    queen: QueenSection = Field(
        default_factory=QueenSection, description="Queen tick and heartbeat cadence."
    )
    hive_stand: HiveStandSection = Field(
        default_factory=HiveStandSection, description="The Hive Stand: the Queen's own machine."
    )
    llm: LlmSection = Field(
        default_factory=LlmSection, description="Model providers and the slot-binding table."
    )
    forage: ForageSection = Field(
        default_factory=ForageSection,
        description="Per-goal ceilings, role footprints, and the map.",
    )
    supervision: SupervisionSection = Field(
        default_factory=SupervisionSection,
        description="Escalation policy files and heartbeat limits.",
    )
    memory: MemorySection = Field(
        default_factory=MemorySection, description="Hot/warm memory tier sizing and retention."
    )
    security: SecuritySection = Field(
        default_factory=SecuritySection, description="Per-Comb-Shield-tier egress posture."
    )
    honey: HoneySection = Field(
        default_factory=HoneySection,
        description="Wraps [honey.clearance]: Honey Clearance defaults and the read/write matrix.",
    )
    brood_chamber: BroodChamberSection = Field(
        default_factory=BroodChamberSection, description="The task graph store's own limit."
    )
    pheromone: PheromoneSection = Field(
        default_factory=PheromoneSection, description="Pheromone Trail retention."
    )
    source_path: Path | None = Field(
        default=None,
        exclude=True,
        description="The file this manifest was loaded from; set by "
        "hivemind.manifest.loader.load_manifest, never by hand. None for a manifest built "
        "directly (tests, or a default-only HiveManifest()).",
    )

    def resolve_path(self, path: Path) -> Path:
        """Resolve `path` relative to this manifest's own directory.

        Every path-shaped field in the manifest (a database file, a scratch root, a policy file)
        is written relative to the manifest itself, so moving a whole Hive's directory never
        breaks its own configuration.

        Args:
            path: A path from a manifest field; already absolute paths are returned unchanged.

        Returns:
            `path` unchanged if it is already absolute; otherwise `path` joined onto this
            manifest's directory (`source_path.parent`), or onto the current working directory
            when `source_path` is None (a manifest built directly, not through `load_manifest`).
        """
        if path.is_absolute():
            return path
        base = self.source_path.parent if self.source_path is not None else Path.cwd()
        return base / path

    @model_validator(mode="after")
    def _forage_map_providers_are_declared(self) -> HiveManifest:
        """Reject a [forage.map] entry whose provider has no [llm.providers] entry."""
        for source_id, spec in self.forage.map.items():
            if spec.provider not in self.llm.providers:
                raise ValueError(
                    f"[forage.map.{source_id}] names provider {spec.provider!r}, which has no "
                    f"[llm.providers.{spec.provider}] entry."
                )
        return self
