"""Define the ``[forage]`` section: goal budgets, per-role footprints, the map, and the reserve.

``ForageSection`` is the manifest's whole picture of Forage (the Hive's capacity, modelled as
data): the per-goal ceilings every grant respects (``grant_ttl_s``, ``spend_cap_per_goal_usd``,
``token_budget_per_goal``, ``max_sub_bees_per_goal``), ``[forage.roles.<role>]`` (what one bee of a
role costs its Cell, keyed by the lowercase ``waggle.messages.task.WorkerRole`` member name --
``hivemind.forage.RoleFootprint`` embedded as-is, since the field-by-field shape is already fixed
there), ``[forage.map.<source_id>]`` (every source that can serve a model --
``hivemind.forage.ModelSourceSpec`` embedded as-is), and ``[forage.reserve]`` (what the Queen holds
back before any grant -- ``hivemind.forage.RoyalReserve`` embedded as-is, whose own defaults are
already sensible for local development). This module's own validator covers only what those three
embedded models cannot check themselves: that every role key actually names a
``WorkerRole`` member and that ``drone`` (the one role phase 3 implements) is always bound. The one
cross-section check the roadmap also calls for -- that every ``[forage.map]`` entry's provider is
declared in ``[llm.providers]`` -- cannot live here, because this section has no visibility into
``[llm]``; ``hivemind.manifest.schema.manifest.HiveManifest`` runs that check once both sections
are in hand.

Fits into the Hive:
    Layer 1 (foundational services; capacity as data). Embedded by
    ``hivemind.manifest.schema.manifest.HiveManifest``. Calls into ``hivemind.forage`` (for
    ``RoleFootprint``, ``ModelSourceSpec`` and ``RoyalReserve``) and ``waggle`` (for
    ``WorkerRole``) only.

Key invariants:
    - Every model here is frozen and forbids unknown fields (codingrules section 8.5).
    - Every key of ``roles`` is a lowercase ``WorkerRole`` member name; ``drone`` is always one of
      them, because the Drone is the only Worker role phase 3 implements
      (``hivemind.workers.roles.drone``).
    - ``ForageSection.map`` and ``ForageSection.roles`` embed ``hivemind.forage`` models directly
      rather than mirroring their fields, so the two can never drift apart (codingrules section
      8.6 corollary: "manifest embeds ModelSourceSpec directly as the model for [forage.map...]").

See Also:
    - .claude/roadmap.md step 3.1 for the field-by-field description this module implements.
    - .claude/codingrules.md section 8.10 for Forage's dimensions, grants and the Royal Reserve.
    - hivemind.forage.models.capacity for RoleFootprint, the model this section's roles values are.
    - hivemind.forage.models.sources for ModelSourceSpec, the model this section's map values are.
    - hivemind.forage.models.grants for RoyalReserve, the model this section's reserve value is.
    - hivemind.manifest.schema.manifest for the cross-section check on [forage.map] providers.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator

from hivemind.forage import ModelSourceSpec, RoleFootprint, RoyalReserve
from waggle.messages.task import WorkerRole

DEFAULT_GRANT_TTL_S = (
    300.0  # Five minutes: long enough to outlast a slow heartbeat, short to renew.
)
DEFAULT_SPEND_CAP_PER_GOAL_USD = 5.0  # A conservative per-goal ceiling for local development.
DEFAULT_TOKEN_BUDGET_PER_GOAL = 2_000_000  # Generous for a multi-step goal without being unbounded.
DEFAULT_MAX_SUB_BEES_PER_GOAL = 4  # Matches the Hive Stand's own typical max_sub_bees default.
REQUIRED_ROLE = "drone"  # The only Worker role phase 3 implements; every manifest must bind it.

__all__ = [
    "DEFAULT_GRANT_TTL_S",
    "DEFAULT_MAX_SUB_BEES_PER_GOAL",
    "DEFAULT_SPEND_CAP_PER_GOAL_USD",
    "DEFAULT_TOKEN_BUDGET_PER_GOAL",
    "REQUIRED_ROLE",
    "ForageSection",
]

# A frozen, extras-forbidding config matching every other section model (codingrules section 8.5).
_MODEL_CONFIG = ConfigDict(frozen=True, extra="forbid")
# Every lowercase WorkerRole member name, computed once for the role-key validator below.
_VALID_ROLE_KEYS = frozenset(role.name.lower() for role in WorkerRole)


class ForageSection(BaseModel):
    """``[forage]``: per-goal ceilings, per-role footprints, the Forage map, and the reserve."""

    model_config = _MODEL_CONFIG

    grant_ttl_s: float = Field(
        default=DEFAULT_GRANT_TTL_S, gt=0, description="Seconds before an unrenewed grant expires."
    )
    spend_cap_per_goal_usd: float = Field(
        default=DEFAULT_SPEND_CAP_PER_GOAL_USD, ge=0, description="The most one goal may spend."
    )
    token_budget_per_goal: int = Field(
        default=DEFAULT_TOKEN_BUDGET_PER_GOAL, gt=0, description="The most tokens one goal may use."
    )
    max_sub_bees_per_goal: int = Field(
        default=DEFAULT_MAX_SUB_BEES_PER_GOAL,
        gt=0,
        description="The most concurrent sub-bees one goal's tasks may spawn in total.",
    )
    roles: dict[str, RoleFootprint] = Field(
        default_factory=dict,
        description="What one bee of a role costs its Cell, keyed by lowercase WorkerRole name.",
    )
    map: dict[str, ModelSourceSpec] = Field(
        default_factory=dict,
        description="Every source that can serve a model, keyed by its Forage map source id. "
        "Every entry's provider must also appear in [llm.providers] (checked on HiveManifest).",
    )
    reserve: RoyalReserve = Field(
        default_factory=RoyalReserve,
        description="What the Queen holds back from the shared pool before any grant.",
    )

    @model_validator(mode="after")
    def _roles_are_worker_roles_and_drone_is_present(self) -> ForageSection:
        """Reject an unknown role key, and require `drone` to always be bound."""
        unknown = [key for key in self.roles if key not in _VALID_ROLE_KEYS]
        if unknown:
            raise ValueError(
                f"[forage.roles] has unknown role keys {unknown}; expected one of "
                f"{sorted(_VALID_ROLE_KEYS)}."
            )
        if REQUIRED_ROLE not in self.roles:
            raise ValueError(
                f"[forage.roles] is missing a {REQUIRED_ROLE!r} entry; the Drone is the only "
                "Worker role phase 3 implements, so every manifest must give it a footprint."
            )
        return self
