"""Define the ``[supervision]`` and ``[memory]`` sections: escalation, heartbeats, hot state.

``SupervisionSection`` names the files a Supervisor (the protocol every level -- human, Queen,
Warden -- shares) loads on start: its escalation policy (``hivemind.supervision.policy.
load_policy``) and Capping's risk-tier table (``hivemind.supervision.capping``), plus the
heartbeat and offline limits every level's liveness tracking uses. ``MemorySection`` sizes the hot
and warm memory tiers (codingrules section 6.1): how much of a bee's context budget is reserved for
retrieved and hot-state content versus the model's own reply, when a Handoff (the document a bee
writes before its context resets) is written, how long Cell Wax (Queen-written per-Cell cautions)
and episodes are kept, and how often a House Bee sweep (demotion, then compaction) runs. Both
sections are grouped in one file because each is a short table of scalars
(codingrules section 5.2: one file per responsibility, not one file per bracket header, once
either responsibility would need more than that). Phase 11 (the Swarm) gives ``[supervision]``
two small sub-tables: ``[supervision.reconnect_budget]``, how long a Warden that lost its link
may spend investigating it with a diagnostic Drone (a short-lived Worker) before it falls back to
the Clustering the offline limit already guarantees, and ``[supervision.watch]``, the cadence,
retention, alarm thresholds and watched directories of a Real Cell's watch mode and its Patrol (a
scheduled read-only review of what the watching Warden observed).

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
    - ``reconnect_budget.wall_clock_s`` is below ``max_offline_s``: the budget is carved out of the
      offline limit, never added to it, so investigating a lost link can never keep a Warden
      holding its grant longer than the limit already allows.
    - ``MemorySection.budget_fraction`` and ``handoff_threshold`` are both fractions in (0, 1]: a
      value of 0 would starve the model of any context and both quantities are "portion of
      something", never a raw count.

See Also:
    - .claude/roadmap.md step 3.1 for the field-by-field description this module implements.
    - .claude/codingrules.md section 6.1 for the memory-tier vocabulary (Hot State, Handoff,
      Cell Wax) this section's field names follow.
    - hivemind.supervision.policy for load_policy, the reader of policy_file.
    - docs/adr/0054-access-levels-watch-mode-and-the-patrol.md for the watch mode `watch` tunes.
    - docs/adr/0056-active-reconnection-diagnostic-drones.md for the budget `reconnect_budget`
      sets.
    - hivemind.supervision.defaults for the two tables an unset field falls back to.
    - hivemind.manifest.schema.manifest for resolve_path, which turns these into absolute paths.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from waggle.messages.cell.wax import MAX_WAX_TEXT_CHARS
from waggle.messages.swarm.watch import MAX_OBSERVE_ROOTS

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
# Four hours: roadmap step 4.2's demotion window. An item unlinked to any active task and past no
# Alarm resolution still earns its place in hot state for a working session or two; past this it
# is almost certainly stale and the House Bee sweep (roadmap step 4.3) moves it to Bee Bread, where
# it is still findable by id, time or task -- nothing is lost, only no longer always-loaded.
DEFAULT_HOT_WINDOW_S = 4.0 * 3600.0
# One hour: how often a House Bee sweep (demotion, then compaction) runs (roadmap step 4.3).
# Shorter than DEFAULT_HOT_WINDOW_S on purpose -- a sweep that runs several times within one
# demotion window is cheap (an empty pass costs one lookup) and keeps Bee Bread from ever
# accumulating a large backlog between runs, which matters once compaction batches are bounded by
# hivemind.memory.bee_bread.entry.MAX_REF_IDS.
DEFAULT_SWEEP_INTERVAL_S = 3_600.0
# Matches waggle.messages.cell.wax.MAX_WAX_TEXT_CHARS: the wire shape's own ceiling on a Cell Wax
# note's text, reused as this section's own default so a manifest that never sets
# wax_text_cap_chars gets exactly the wire limit, never a silently different number.
DEFAULT_WAX_TEXT_CAP_CHARS = MAX_WAX_TEXT_CHARS
DEFAULT_RECONNECT_ATTEMPTS = 2  # Two tries at diagnosing a lost link: one fix, one second look.
# Five minutes: half the default offline limit, so a diagnostic Drone gets a real chance without
# the Warden holding a grant it may lose any longer than max_offline_s allows (ADR-0056).
DEFAULT_RECONNECT_WALL_CLOCK_S = 300.0
DEFAULT_WATCH_INTERVAL_S = 300.0  # Five minutes between observation samples of a watched Cell.
DEFAULT_WATCH_RETENTION_S = 86_400.0  # A day: observations are C2 and short-lived on purpose.
DEFAULT_PATROL_INTERVAL_S = 3_600.0  # An hour between Patrols (one awake episode each).
DEFAULT_CPU_SHARE_ALARM = 0.5  # A new process using half the device's CPU is worth a look.
DEFAULT_DISK_FILL_ALARM = 0.95  # A disk 95% full is about to break something.

__all__ = [
    "DEFAULT_ALARM_ATTEMPT_LIMIT",
    "DEFAULT_BUDGET_FRACTION",
    "DEFAULT_CELL_WAX_CAP",
    "DEFAULT_CPU_SHARE_ALARM",
    "DEFAULT_DISK_FILL_ALARM",
    "DEFAULT_EPISODE_RETENTION_S",
    "DEFAULT_EXPIRY_S",
    "DEFAULT_HANDOFF_THRESHOLD",
    "DEFAULT_HEARTBEAT_INTERVAL_S",
    "DEFAULT_HEARTBEAT_MISS_LIMIT",
    "DEFAULT_HOT_WINDOW_S",
    "DEFAULT_ITEM_CAP_CHARS",
    "DEFAULT_MAX_OFFLINE_S",
    "DEFAULT_OUTPUT_RESERVE_TOKENS",
    "DEFAULT_PATROL_INTERVAL_S",
    "DEFAULT_RECONNECT_ATTEMPTS",
    "DEFAULT_RECONNECT_WALL_CLOCK_S",
    "DEFAULT_SWEEP_INTERVAL_S",
    "DEFAULT_WATCH_INTERVAL_S",
    "DEFAULT_WATCH_RETENTION_S",
    "DEFAULT_WAX_TEXT_CAP_CHARS",
    "MemorySection",
    "ReconnectBudgetSection",
    "SupervisionSection",
    "WatchSection",
    "require_budget_inside_offline_limit",
]

# A frozen, extras-forbidding config matching every other section model (codingrules section 8.5).
_MODEL_CONFIG = ConfigDict(frozen=True, extra="forbid")


class ReconnectBudgetSection(BaseModel):
    """``[supervision.reconnect_budget]``: what a Warden may spend diagnosing a lost link."""

    model_config = _MODEL_CONFIG

    attempts: int = Field(
        default=DEFAULT_RECONNECT_ATTEMPTS,
        ge=1,
        description="Diagnostic Drones a Warden may send, one after another, to find out why "
        "its link to the Queen (or a device's, for the Queen's own Warden) was lost and fix it "
        "if it can; at least 1.",
    )
    wall_clock_s: float = Field(
        default=DEFAULT_RECONNECT_WALL_CLOCK_S,
        gt=0,
        description="Seconds all those attempts may take together. It is carved out of "
        "max_offline_s, never added to it, so it must be below it; when it runs out the Warden "
        "falls back to Clustering, which keeps the same deadline as a backstop.",
    )


def require_budget_inside_offline_limit(
    budget: ReconnectBudgetSection, max_offline_s: float
) -> None:
    """Raise unless `budget` is carved out of `max_offline_s` rather than added to it.

    The one rule both the Hive's ``[supervision]`` and a device manifest's hold the budget to
    (ADR-0056), kept in one place so the two can never disagree about what "carved out" means.

    Args:
        budget: The ``[supervision.reconnect_budget]`` table being validated.
        max_offline_s: The ``[supervision] max_offline_s`` it sits beside.

    Raises:
        ValueError: The budget's wall clock is not below the offline limit; the message names both
            figures so an operator can see which to change.
    """
    # The budget is part of the offline limit, not extra time on top of it: a Warden that
    # spent as long diagnosing as the limit allows would have nothing left to cluster with.
    if budget.wall_clock_s >= max_offline_s:
        raise ValueError(
            f"[supervision.reconnect_budget] wall_clock_s "
            f"({budget.wall_clock_s:g}) must be below [supervision] "
            f"max_offline_s ({max_offline_s:g}): the budget is carved out of the offline "
            "limit. Lower wall_clock_s or raise max_offline_s."
        )


class WatchSection(BaseModel):
    """``[supervision.watch]``: a watching Warden's sampling, retention, Patrol and alarms."""

    model_config = _MODEL_CONFIG

    interval_s: float = Field(
        default=DEFAULT_WATCH_INTERVAL_S,
        gt=0,
        description="Seconds between two observation samples (processes, resources, logs, file "
        "changes) of a Real Cell the Warden watches while it has no bees working.",
    )
    retention_s: float = Field(
        default=DEFAULT_WATCH_RETENTION_S,
        gt=0,
        description="Seconds an observation is kept in Bee Bread (labelled C2) before the sweep "
        "may purge it; keep it at least patrol_interval_s, or a Patrol reviews only the newest "
        "part of what it covers.",
    )
    patrol_interval_s: float = Field(
        default=DEFAULT_PATROL_INTERVAL_S,
        gt=0,
        description="Seconds between two Patrols: one awake episode over the observations since "
        "the last, which raises an Alarm, deposits Nectar, proposes Cell Wax or finds nothing "
        "notable.",
    )
    cpu_share_alarm: float = Field(
        default=DEFAULT_CPU_SHARE_ALARM,
        gt=0,
        le=1,
        description="The share of the device's CPU, between 0 and 1, above which a newly seen "
        "process is labelled a fact for the Patrol to weigh.",
    )
    disk_fill_alarm: float = Field(
        default=DEFAULT_DISK_FILL_ALARM,
        gt=0,
        le=1,
        description="The fraction of the device's disk, between 0 and 1, above which the Patrol "
        "is told the disk is nearly full.",
    )
    roots: tuple[str, ...] = Field(
        default=(),
        max_length=MAX_OBSERVE_ROOTS,
        description="Absolute directories on the Hive Stand whose log files and file changes its "
        "own Warden watches while it has no bees working; none watches only processes and "
        "resource use. A Swarm device's directories are not here: the operator names them for "
        "each device with `hive swarm watch`.",
    )

    @field_validator("roots")
    @classmethod
    def _roots_are_absolute(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        """Reject a root that is relative or holds a NUL byte."""
        # The watcher hands each root to a command as an argument: a relative one would mean
        # whatever the command's working directory is, and a NUL cannot be an argument at all.
        for root in value:
            if not Path(root).is_absolute() or "\0" in root:
                raise ValueError(f"[supervision.watch] roots must be absolute paths, got {root!r}.")
        return value


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
        description="Seconds between a supervised child's heartbeat reports. The Queen judges each "
        "Warden by the interval its own Heartbeat declares, never less than this, and her tick "
        "waits no longer than this with nothing arriving.",
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
    reconnect_budget: ReconnectBudgetSection = Field(
        default_factory=ReconnectBudgetSection,
        description="What a Warden that lost its link may spend diagnosing it before it falls "
        "back to Clustering; carved out of max_offline_s.",
    )
    watch: WatchSection = Field(
        default_factory=WatchSection,
        description="A Real Cell's watch mode and Patrol: sampling, retention and alarm "
        "thresholds.",
    )

    @model_validator(mode="after")
    def _reconnect_budget_fits_inside_max_offline(self) -> SupervisionSection:
        """Reject a reconnection budget that is not shorter than the offline limit."""
        require_budget_inside_offline_limit(self.reconnect_budget, self.max_offline_s)
        return self


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
    wax_text_cap_chars: int = Field(
        default=DEFAULT_WAX_TEXT_CAP_CHARS,
        gt=0,
        description="The most characters one Cell Wax note's text holds; a Hive may cap this "
        "lower than waggle's own MAX_WAX_TEXT_CHARS ceiling, never higher (the wire shape itself "
        "refuses more).",
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
    hot_window_s: float = Field(
        default=DEFAULT_HOT_WINDOW_S,
        gt=0,
        description="Seconds an item may sit in hot state, unlinked to an active task and past no "
        "Alarm resolution, before the House Bee sweep ages it out to Bee Bread.",
    )
    pins: tuple[str, ...] = Field(
        default=(),
        description="Operator-set facts always included in hot state, regardless of age.",
    )
    sweep_interval_s: float = Field(
        default=DEFAULT_SWEEP_INTERVAL_S,
        gt=0,
        description="Seconds between one House Bee sweep (demotion, then compaction) and the "
        "next; hivemind.workers.roles.house_bee.SweepSchedule reads this to decide when the next "
        "sweep is due.",
    )
