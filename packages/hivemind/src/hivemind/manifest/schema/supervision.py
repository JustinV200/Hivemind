"""Define the ``[supervision]`` and ``[memory]`` sections: escalation, heartbeats, hot state.

``SupervisionSection`` names the files a Supervisor (the protocol every level -- human, Queen,
Warden -- shares) loads on start: its escalation policy (``hivemind.supervision.policy.
load_policy``) and Capping's risk-tier table (``hivemind.supervision.capping``), plus the
heartbeat and offline limits every level's liveness tracking uses. ``MemorySection`` sizes the hot
and warm memory tiers (codingrules section 6.1): how much of a bee's context budget is reserved for
retrieved and hot-state content versus the model's own reply, when a Handoff (the document a bee
writes before its context resets) is written, and how long Cell Wax (Queen-written per-Cell
cautions) and episodes are kept. Both sections are grouped in one file because each is a short,
flat table of scalars with no embedded sub-models and no cross-field validation of its own
(codingrules section 5.2: one file per responsibility, not one file per bracket header, once
either responsibility would need more than that).

Fits into the Hive:
    Layer 1 (foundational services; capacity as data). Embedded by
    ``hivemind.manifest.schema.manifest.HiveManifest``. Calls into nothing beyond the standard
    library and pydantic; the files these paths name are loaded elsewhere
    (``hivemind.supervision.policy.load_policy``, ``hivemind.supervision.capping.load_tiers``).

Key invariants:
    - Every model here is frozen and forbids unknown fields (codingrules section 8.5).
    - ``SupervisionSection.policy_file`` and ``capping_tiers_file`` are both ``None`` by default,
      meaning "use the table shipped in ``hivemind.supervision.defaults``". They have no path
      default because a relative one can only ever be right for a manifest in one particular
      directory, and every Hive's manifest lives somewhere different.
    - When either *is* set it is resolved relative to the manifest's own directory by
      ``HiveManifest.resolve_path``, never read here: this module only names the paths, since
      reading a file at schema-validation time would make loading a manifest depend on the
      filesystem layout of whatever loads it next.
    - ``MemorySection.budget_fraction`` and ``handoff_threshold`` are both fractions in (0, 1]: a
      value of 0 would starve the model of any context and both quantities are "portion of
      something", never a raw count.

See Also:
    - .claude/roadmap.md step 3.1 for the field-by-field description this module implements.
    - .claude/codingrules.md section 6.1 for the memory-tier vocabulary (Hot State, Handoff,
      Cell Wax) this section's field names follow.
    - hivemind.supervision.policy for load_policy, the reader of policy_file.
    - hivemind.supervision.defaults for the two tables an unset field falls back to.
    - hivemind.manifest.schema.manifest for resolve_path, which turns these into absolute paths.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

DEFAULT_HEARTBEAT_INTERVAL_S = 5.0  # Matches [queen] heartbeat_interval_s; a Warden's own cadence.
DEFAULT_HEARTBEAT_MISS_LIMIT = 3  # Three missed beats before a child is treated as stalled.
DEFAULT_MAX_OFFLINE_S = (
    600.0  # Ten minutes: past this, an offline Warden's grant is treated as lost.
)
DEFAULT_ALARM_ATTEMPT_LIMIT = (
    3  # Escalation policy rows rarely need more attempts than this to fire.
)
DEFAULT_BUDGET_FRACTION = 0.6  # Most of a request's context window goes to retrieved/hot content.
DEFAULT_OUTPUT_RESERVE_TOKENS = (
    4_096  # Reserved so a long reply is never truncated by its own prompt.
)
DEFAULT_HANDOFF_THRESHOLD = 0.66  # Checkpoint before two-thirds of the window fills, not after.
DEFAULT_ITEM_CAP_CHARS = (
    4_000  # One hot-state item's cap: a paragraph or two, not a whole document.
)
DEFAULT_CELL_WAX_CAP = 20  # Cautions per Cell before the oldest, least-severe ones are dropped.
DEFAULT_EXPIRY_S = 604_800  # Seven days: a pin or a caution that outlives a week is probably stale.
DEFAULT_EPISODE_RETENTION_S = (
    604_800  # Seven days of episode records kept for the Observation Hive.
)

__all__ = [
    "DEFAULT_ALARM_ATTEMPT_LIMIT",
    "DEFAULT_BUDGET_FRACTION",
    "DEFAULT_CELL_WAX_CAP",
    "DEFAULT_EPISODE_RETENTION_S",
    "DEFAULT_EXPIRY_S",
    "DEFAULT_HANDOFF_THRESHOLD",
    "DEFAULT_HEARTBEAT_INTERVAL_S",
    "DEFAULT_HEARTBEAT_MISS_LIMIT",
    "DEFAULT_ITEM_CAP_CHARS",
    "DEFAULT_MAX_OFFLINE_S",
    "DEFAULT_OUTPUT_RESERVE_TOKENS",
    "MemorySection",
    "SupervisionSection",
]

# A frozen, extras-forbidding config matching every other section model (codingrules section 8.5).
_MODEL_CONFIG = ConfigDict(frozen=True, extra="forbid")


class SupervisionSection(BaseModel):
    """``[supervision]``: escalation policy file, heartbeat cadence, and offline limits."""

    model_config = _MODEL_CONFIG

    policy_file: Path | None = Field(
        default=None,
        description="An EscalationPolicy TOML file overriding the one shipped in "
        "hivemind.supervision.defaults, resolved relative to the manifest's own directory "
        "(HiveManifest.resolve_path).",
    )
    capping_tiers_file: Path | None = Field(
        default=None,
        description="A Capping risk-tier TOML file overriding the shipped one, resolved the same "
        "way as policy_file.",
    )
    heartbeat_interval_s: float = Field(
        default=DEFAULT_HEARTBEAT_INTERVAL_S,
        gt=0,
        description="Seconds between a supervised child's heartbeat reports.",
    )
    heartbeat_miss_limit: int = Field(
        default=DEFAULT_HEARTBEAT_MISS_LIMIT,
        gt=0,
        description="Missed heartbeats before a child is treated as stalled.",
    )
    max_offline_s: float = Field(
        default=DEFAULT_MAX_OFFLINE_S,
        gt=0,
        description="Seconds an offline Warden may stay disconnected before its grant is lost.",
    )
    alarm_attempt_limit: int = Field(
        default=DEFAULT_ALARM_ATTEMPT_LIMIT,
        gt=0,
        description="A ceiling on attempts an escalation policy row is expected to need.",
    )


class MemorySection(BaseModel):
    """``[memory]``: hot/warm tier sizing, Handoff timing, and Cell Wax and episode retention."""

    model_config = _MODEL_CONFIG

    budget_fraction: float = Field(
        default=DEFAULT_BUDGET_FRACTION,
        gt=0,
        le=1,
        description="Fraction of a request's context window reserved for assembled content.",
    )
    output_reserve_tokens: int = Field(
        default=DEFAULT_OUTPUT_RESERVE_TOKENS,
        gt=0,
        description="Tokens reserved for the model's own reply, never spent on assembled content.",
    )
    handoff_threshold: float = Field(
        default=DEFAULT_HANDOFF_THRESHOLD,
        gt=0,
        le=1,
        description="Fraction of the context budget used at which a bee checkpoints via Handoff.",
    )
    item_cap_chars: int = Field(
        default=DEFAULT_ITEM_CAP_CHARS,
        gt=0,
        description="The most characters one hot-state item holds.",
    )
    cell_wax_cap: int = Field(
        default=DEFAULT_CELL_WAX_CAP, gt=0, description="Cell Wax cautions kept per Cell."
    )
    default_expiry_s: float = Field(
        default=DEFAULT_EXPIRY_S,
        gt=0,
        description="Default seconds before a pin or caution expires.",
    )
    episode_retention_s: float = Field(
        default=DEFAULT_EPISODE_RETENTION_S,
        gt=0,
        description="Seconds an EpisodeRecord is kept before it may be purged.",
    )
    pins: tuple[str, ...] = Field(
        default=(),
        description="Operator-set facts always included in hot state, regardless of age.",
    )
