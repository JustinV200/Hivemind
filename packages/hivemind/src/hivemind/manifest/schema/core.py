"""Define the Hive Manifest's identity and lifecycle sections: hive, queen, hive_stand.

A Hive Manifest is the TOML file that describes one running Hive: a Queen (the central
orchestrator), its Wardens (per-Cell supervisors) and the Cells (units of compute a Worker runs
on) they supervise. This module holds the sections every Hive needs regardless of which model
providers or security posture it runs with: ``HiveSection`` (the Hive's own identity: its id, its
node id, and where its SQLite file lives), ``QueenSection`` (how often the Queen ticks and checks
in), ``HiveStandSection`` (the Hive Stand, the machine the Queen runs on and the first Real Cell:
its own capacity-probe overrides in ``HiveStandCapacityOverrides``, its per-lease scratch quota
and disk reserve, and the wire ``AccessLevel`` any lease on it may hold at most),
``BroodChamberSection`` (the task graph store's one limit) and ``PheromoneSection`` (how long the
audit trail keeps events). Every field here carries a default sensible for local development, so a
manifest that omits every section but ``[hive]`` still loads
(``hivemind.manifest.schema.manifest.HiveManifest`` gives every section but ``hive`` a default;
``hive`` alone has no sensible default because a Hive's own identity cannot be guessed).

Fits into the Hive:
    Layer 1 (foundational services; capacity as data). Embedded by
    ``hivemind.manifest.schema.manifest.HiveManifest``, the root model ``hivemind.manifest.loader``
    validates a TOML document into. Calls into ``waggle`` only, for the id field aliases and the
    Waggle URI check the Hive Stand's address must pass.

Key invariants:
    - Every model here is frozen and forbids unknown fields (codingrules section 8.5).
    - HiveSection.id and HiveSection.node_id are the only two fields in this whole package with no
      default: a Hive's identity is never guessed, so a manifest missing ``[hive]`` or either of
      those two keys fails validation rather than silently minting one.
    - HiveStandSection.address is checked by ``waggle.check_waggle_uri``: ``wss://`` from
      anywhere, ``ws://`` only on a loopback host, exactly the rule every other dialable endpoint
      in the Hive follows.

See Also:
    - .claude/codingrules.md section 13 for "all configuration is a Hive Manifest" and the
      ``[hive_stand]`` address rule.
    - .claude/roadmap.md step 3.1 for the field-by-field description this module implements.
    - waggle.uris for check_waggle_uri, the validator HiveStandSection.address uses.
    - hivemind.manifest.schema.manifest for HiveManifest, the root model this is embedded in.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from waggle.messages.base import HiveIdField, NodeIdField
from waggle.messages.labels import AccessLevel as WireAccessLevel
from waggle.uris import check_waggle_uri

MAX_HIVE_NAME_CHARS = 128  # A human-facing label, not an id; a page title's worth of room.
DEFAULT_HIVE_DB = (
    "hive.sqlite3"  # ADR-0006: one SQLite file per Hive, beside the manifest by default.
)
DEFAULT_TICK_INTERVAL_S = 0.25  # Fast enough that the Queen's inbox never visibly lags a human.
DEFAULT_HEARTBEAT_INTERVAL_S = (
    5.0  # Frequent enough that a stalled Warden is caught within seconds.
)
DEFAULT_MAX_AWAKE_PER_MINUTE = (
    30  # A budget against a runaway autopilot rule waking the Queen in a loop.
)
DEFAULT_SCRATCH_ROOT = ".hive/scratch"  # Relative to the manifest's own directory (resolve_path).
DEFAULT_HIVE_STAND_ADDRESS = (
    "ws://127.0.0.1:8720"  # Loopback by default; check_waggle_uri allows it.
)
DEFAULT_SCRATCH_QUOTA_MB = 4096  # Roadmap step 3.11's own default: 4 GiB per lease's scratch dir.
DEFAULT_DISK_RESERVE_MB = 1024  # A new lease refuses when free disk drops under this, in MiB.
# Least-privilege default: the Hive Stand is the operator's own machine, so a lease may write
# its own scratch directory but not the whole device unless the operator explicitly widens it.
DEFAULT_HIVE_STAND_ACCESS_LEVEL = WireAccessLevel.SCRATCH
DEFAULT_MAX_GRAPH_TASKS = 64  # A single goal's task graph rarely needs more nodes than this.
DEFAULT_RETENTION_DAYS = (
    90  # A quarter of trail history kept before the retention job may delete it.
)

__all__ = [
    "DEFAULT_DISK_RESERVE_MB",
    "DEFAULT_HEARTBEAT_INTERVAL_S",
    "DEFAULT_HIVE_DB",
    "DEFAULT_HIVE_STAND_ACCESS_LEVEL",
    "DEFAULT_HIVE_STAND_ADDRESS",
    "DEFAULT_MAX_AWAKE_PER_MINUTE",
    "DEFAULT_MAX_GRAPH_TASKS",
    "DEFAULT_RETENTION_DAYS",
    "DEFAULT_SCRATCH_QUOTA_MB",
    "DEFAULT_SCRATCH_ROOT",
    "DEFAULT_TICK_INTERVAL_S",
    "MAX_HIVE_NAME_CHARS",
    "BroodChamberSection",
    "HiveSection",
    "HiveStandCapacityOverrides",
    "HiveStandSection",
    "PheromoneSection",
    "QueenSection",
]

# A frozen, extras-forbidding config every section model in this module shares (codingrules 8.5).
_MODEL_CONFIG = ConfigDict(frozen=True, extra="forbid")


class HiveSection(BaseModel):
    """``[hive]``: the Hive's own identity. The only section with no sensible default.

    Every other section in the manifest may be omitted and still validate; this one may not,
    because a Hive's id and node id are its identity, never guessed on its behalf.
    """

    model_config = _MODEL_CONFIG

    id: HiveIdField = Field(description="This Hive's identity; minted once, never regenerated.")
    node_id: NodeIdField = Field(
        description="This process's node identity, used to segment the Pheromone Trail and to "
        "address it on Waggle envelopes."
    )
    name: str = Field(
        default="",
        max_length=MAX_HIVE_NAME_CHARS,
        description="A human-facing label; empty for none.",
    )
    env: Literal["dev", "prod"] = Field(
        default="dev", description="Deployment environment; gates dev-only escape hatches."
    )
    db: Path = Field(
        default=Path(DEFAULT_HIVE_DB),
        description="Where this Hive's single SQLite file lives, resolved relative to the "
        "manifest's own directory (HiveManifest.resolve_path).",
    )


class QueenSection(BaseModel):
    """``[queen]``: how often the Queen's tick loop runs and checks in."""

    model_config = _MODEL_CONFIG

    tick_interval_s: float = Field(
        default=DEFAULT_TICK_INTERVAL_S, gt=0, description="Seconds between Queen tick loop passes."
    )
    heartbeat_interval_s: float = Field(
        default=DEFAULT_HEARTBEAT_INTERVAL_S,
        gt=0,
        description="Seconds between the Queen's own heartbeat reports.",
    )
    max_awake_per_minute: int = Field(
        default=DEFAULT_MAX_AWAKE_PER_MINUTE,
        gt=0,
        description="A ceiling on awake episodes per minute, against a runaway autopilot rule.",
    )


class HiveStandCapacityOverrides(BaseModel):
    """``[hive_stand.capacity]``: manual overrides for the Hive Stand's own capacity probe.

    Every field is None by default, meaning "trust the probe"; setting one overrides only that
    figure, for a host whose probe under- or over-reports (a container with a cgroup limit the
    probe cannot see, for instance).
    """

    model_config = _MODEL_CONFIG

    max_sub_bees: int | None = Field(
        default=None, gt=0, description="Override the Hive Stand's own concurrent sub-bee cap."
    )
    cores: int | None = Field(
        default=None, gt=0, description="Override the probed logical core count."
    )
    memory_bytes: int | None = Field(
        default=None, gt=0, description="Override the probed total memory, in bytes."
    )


class HiveStandSection(BaseModel):
    """``[hive_stand]``: the machine the Queen runs on, and the first Real Cell.

    ``enabled = false`` refuses new leases on the Hive Stand; it does not remove the Warden that
    always exists for it (codingrules section 4 corollary: "the Hive Stand's Warden exists
    whenever the Queen runs").
    """

    model_config = _MODEL_CONFIG

    enabled: bool = Field(
        default=True, description="Whether the Hive Stand may be leased as a Real Cell."
    )
    scratch_root: Path = Field(
        default=Path(DEFAULT_SCRATCH_ROOT),
        description="Where per-lease scratch directories live, resolved relative to the "
        "manifest's own directory (HiveManifest.resolve_path).",
    )
    address: str = Field(
        default=DEFAULT_HIVE_STAND_ADDRESS,
        description="The Queen's own address: the URL Wardens and Pollen Packets dial to reach "
        "it. Supersedure (moving the Hive Stand) rewrites this value on every node.",
    )
    capacity: HiveStandCapacityOverrides = Field(
        default_factory=HiveStandCapacityOverrides,
        description="Manual overrides for the Hive Stand's own capacity probe.",
    )
    scratch_quota_mb: int = Field(
        default=DEFAULT_SCRATCH_QUOTA_MB,
        gt=0,
        description="The most bytes, in megabytes, any one lease's scratch directory may grow "
        "to before its command is killed and a QUOTA_EXCEEDED Alarm is raised.",
    )
    disk_reserve_mb: int = Field(
        default=DEFAULT_DISK_RESERVE_MB,
        ge=0,
        description="Free disk, in megabytes, a new lease refuses to dip below.",
    )
    access_level: WireAccessLevel = Field(
        default=DEFAULT_HIVE_STAND_ACCESS_LEVEL,
        description="The most access any lease on the Hive Stand may hold. This is the wire "
        "label (waggle.messages.labels.AccessLevel), not hivemind.cell.tiers.AccessLevel: this "
        "module is Layer 1 and may not import hivemind.cell (Layer 2); "
        "hivemind.cell.local.HiveStandConfig.from_section converts it via the hivemind mirror's "
        "own from_wire.",
    )

    @field_validator("address")
    @classmethod
    def _address_is_a_dialable_waggle_uri(cls, value: str) -> str:
        """Reject an address Waggle could never dial: not wss://, or ws:// off loopback."""
        # Same rule every other Waggle endpoint in the Hive follows (waggle.uris' own docstring);
        # applying it here means a bad address fails at load time, not on the first dial attempt.
        return check_waggle_uri(value)


class BroodChamberSection(BaseModel):
    """``[brood_chamber]``: the task graph store's one manifest-level limit."""

    model_config = _MODEL_CONFIG

    max_graph_tasks: int = Field(
        default=DEFAULT_MAX_GRAPH_TASKS,
        gt=0,
        description="The most tasks one goal's task graph may hold at once.",
    )


class PheromoneSection(BaseModel):
    """``[pheromone]``: how long the Pheromone Trail (the audit log) keeps events."""

    model_config = _MODEL_CONFIG

    retention_days: int = Field(
        default=DEFAULT_RETENTION_DAYS,
        gt=0,
        description="Days a trail event is kept before the retention job may delete it.",
    )
