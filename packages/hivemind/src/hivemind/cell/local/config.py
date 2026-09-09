"""Define HiveStandConfig: the Hive Stand's own settings, converted once from the manifest section.

The Hive Manifest's `[hive_stand]` section (`hivemind.manifest.schema.core.HiveStandSection`) is a
Layer 1 model and may not import `hivemind.cell` (Layer 2), so it carries its security tier as the
plain wire enum and its paths as manifest-relative strings. `HiveStandConfig.from_section` is the
one place that gap is closed: it resolves `scratch_root` against the manifest's own directory and
converts the wire `AccessLevel` into this package's own mirror (`hivemind.cell.tiers`), so every
other module in `hivemind.cell.local` works from one small, already-resolved, already-typed value
instead of reaching back into the manifest itself.

Fits into the Hive:
    Layer 2 (the Cell abstraction), inside `hivemind.cell.local` (the Hive Stand). Built once by
    the composition root (`cli/stores.py`, a later step) from the loaded `HiveManifest`; read by
    `hivemind.cell.local.probe.probe_host` and `hivemind.cell.local.source.HiveStandSource`.
    Calls into `hivemind.cell.tiers` and `hivemind.manifest.schema.core` only.

Key invariants:
    - `scratch_root` is always absolute once `from_section` builds it: a relative
      `HiveStandSection.scratch_root` is joined onto `manifest_dir` first.
    - `comb_shield` defaults to MEADOW and `from_section` never overrides it: the Hive Stand's own
      `[hive_stand]` section carries no comb_shield field this phase (codingrules section 8.7's
      "operator-set at enrolment" is a later phase's `[hive_stand]` addition).

See Also:
    - .claude/roadmap.md step 3.11 for the Hive Stand's own manifest-derived settings.
    - .claude/codingrules.md section 8.7 for AccessLevel and CombShieldLevel, the two tiers here.
    - hivemind.manifest.schema.core for HiveStandSection, the manifest model this converts from.
    - hivemind.cell.local.probe for probe_host, which reads the `cores`/`memory_bytes`/
      `max_sub_bees` overrides carried here.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from hivemind.cell.tiers import AccessLevel, CombShieldLevel
from hivemind.manifest.schema.core import HiveStandSection

__all__ = ["HiveStandConfig"]

# A frozen, extras-forbidding config every model in this module shares (codingrules section 8.5).
_MODEL_CONFIG = ConfigDict(frozen=True, extra="forbid")


class HiveStandConfig(BaseModel):
    """The Hive Stand's own settings, already resolved and typed for this package's own use.

    Built once, by `from_section`, from the loaded manifest's `[hive_stand]` section; every other
    module in `hivemind.cell.local` takes one of these rather than a `HiveStandSection` directly.
    """

    model_config = _MODEL_CONFIG

    enabled: bool = Field(description="Whether the Hive Stand may be leased as a Real Cell.")
    scratch_root: Path = Field(
        description="Absolute directory holding every lease's own scratch subdirectory."
    )
    scratch_quota_mb: int = Field(
        gt=0, description="Per-lease scratch directory cap, in megabytes."
    )
    disk_reserve_mb: int = Field(
        ge=0, description="Free disk, in megabytes, a new lease refuses to dip below."
    )
    max_sub_bees: int | None = Field(
        description="Override for the probed sub-bee cap; None trusts the probe's own default."
    )
    cores: int | None = Field(
        description="Override for the probed logical core count; None trusts the probe."
    )
    memory_bytes: int | None = Field(
        description="Override for the probed total memory, in bytes; None trusts the probe."
    )
    access_level: AccessLevel = Field(
        description="The most access any lease on the Hive Stand may hold."
    )
    comb_shield: CombShieldLevel = Field(
        default=CombShieldLevel.MEADOW, description="The Hive Stand's own security tier."
    )

    @classmethod
    def from_section(cls, section: HiveStandSection, manifest_dir: Path) -> HiveStandConfig:
        """Build a HiveStandConfig from the manifest's `[hive_stand]` section.

        Args:
            section: The loaded `[hive_stand]` section.
            manifest_dir: The manifest's own directory, against which a relative `scratch_root`
                resolves (mirrors `HiveManifest.resolve_path`, without needing the whole manifest).

        Returns:
            A HiveStandConfig with an absolute `scratch_root` and this package's own
            `AccessLevel` mirror in place of the section's wire enum.
        """
        scratch_root = section.scratch_root
        # HiveManifest.resolve_path's own rule: a path already absolute is left alone, otherwise
        # it is joined onto the manifest's own directory so a whole Hive directory can move.
        if not scratch_root.is_absolute():
            scratch_root = manifest_dir / scratch_root
        return cls(
            enabled=section.enabled,
            scratch_root=scratch_root.resolve(strict=False),
            scratch_quota_mb=section.scratch_quota_mb,
            disk_reserve_mb=section.disk_reserve_mb,
            max_sub_bees=section.capacity.max_sub_bees,
            cores=section.capacity.cores,
            memory_bytes=section.capacity.memory_bytes,
            access_level=AccessLevel.from_wire(section.access_level),
        )
