"""Model capacity as data, including the ModelSlot and Tempo types llm depends on: forage.

HostCapacity, a Seat, RoleFootprint, ForageGrant and ForageRequest, the Forage map of every
source that can serve a model, and the ModelSlot (a named place in a routing ladder a model call
can resolve to) and Tempo (the speed-versus-accuracy dial for a slot) types all live here. It
never imports llm, so a grant or a routing input can name a model slot without pulling in the
provider machinery. Phase 2 step 2.3a built Tempo and its AccuracyBar first, phase 3 step 3.4
added ModelSlot and Effort ahead of the capacity types, and phase 3 step 3.12 adds those capacity
types themselves (`hivemind.forage.models`), the Forage map (`hivemind.forage.map`), the pure
allocator (`hivemind.forage.allocate`) and the Forage grant's state machine
(`hivemind.forage.grant_state`), because everything else in this package is pure data with no
dependency on the rest of the Hive.

Fits into the Hive:
    Layer 1 (foundational services; capacity as data). Called by queen when it divides capacity
    and llm when it resolves a slot. Calls into hivemind.common and waggle only; it never
    imports hivemind.llm.

Key invariants:
    - AccuracyBar mirrors waggle.messages.labels.AccuracyBar member for member
      (tests/unit/forage/test_tempo.py checks it).
    - Tempo.latency_budget_s is either None or a number strictly greater than zero; never zero or
      negative.
    - ModelSlot's value is its own UPPER_SNAKE name (the wire label) and its lowercase name is
      the ``[llm.slots]`` manifest key; Effort mirrors waggle.messages.forage.Effort member for
      member (tests/unit/forage/test_slots.py checks both).
    - ForageRequestKind mirrors waggle.messages.forage.values.ForageRequestKind member for member
      (tests/unit/forage/models/test_grants.py checks it).
    - Every model here is frozen, forbids extra fields, and documents every field with
      Field(description=...).
    - GrantState's TRANSITIONS (hivemind.forage.grant_state) is the only place a Forage grant's
      lifecycle edges are decided (codingrules Appendix C, "Forage grant" row).

See Also:
    - .claude/codingrules.md section 4 for the layer 1 row this package occupies.
    - .claude/codingrules.md section 8.10 for Forage's dimensions, grants and the two pools.
    - .claude/codingrules.md section 8.14 for Tempo's role in routing, Forage and Capping.
    - .claude/roadmap.md phase 2 step 2.3a and phase 3 steps 3.4 and 3.12 for the work that
      populates this package.

Public API:
    - AccuracyBar, Tempo, grade_floor: a task's speed-against-accuracy setting and the grade
      floor it implies (hivemind.forage.tempo).
    - ModelSlot, Effort: the named place a model call resolves to, and how hard it tries
      (hivemind.forage.slots).
    - Capacity models: GpuInfo, HostCapacity, Seat, RoleFootprint, ForageCapacity
      (hivemind.forage.models.capacity).
    - Forage map models: ModelCost, ModelSourceSpec, Distance, Abundance, ModelSource
      (hivemind.forage.models.sources).
    - Grant models: AllowedBinding, SeatReservation, ForageGrant, ForageRequest,
      ForageRequestKind, RoyalReserve (hivemind.forage.models.grants).
    - Local pool and hosting models: LocalPool, Ceilings, SourceChain, SlotPlan, HostingPlan
      (hivemind.forage.models.pools).
    - ForageMap, SlotBinding: the catalogue of Forage map sources and its live figures
      (hivemind.forage.map).
    - GrantInputs, GoalBudgets, grant: the pure v0 allocator (hivemind.forage.allocate).
    - GrantState, can_transition, assert_transition, is_terminal: the Forage grant state machine
      (hivemind.forage.grant_state).
    - ForageError, UnknownSourceError, AllocationError, InvalidGrantTransitionError: this
      package's error tree (hivemind.forage.errors).
"""

from hivemind.forage.allocate import GoalBudgets, GrantInputs, grant
from hivemind.forage.errors import (
    AllocationError,
    ForageError,
    InvalidGrantTransitionError,
    UnknownSourceError,
)
from hivemind.forage.grant_state import GrantState, assert_transition, can_transition, is_terminal
from hivemind.forage.map import ForageMap, SlotBinding
from hivemind.forage.models import (
    Abundance,
    AllowedBinding,
    Ceilings,
    Distance,
    ForageCapacity,
    ForageGrant,
    ForageRequest,
    ForageRequestKind,
    GpuInfo,
    HostCapacity,
    HostingPlan,
    LocalPool,
    ModelCost,
    ModelSource,
    ModelSourceSpec,
    RoleFootprint,
    RoyalReserve,
    Seat,
    SeatReservation,
    SlotPlan,
    SourceChain,
)
from hivemind.forage.slots import Effort, ModelSlot
from hivemind.forage.tempo import AccuracyBar, Tempo, grade_floor

__all__ = [
    "Abundance",
    "AccuracyBar",
    "AllocationError",
    "AllowedBinding",
    "Ceilings",
    "Distance",
    "Effort",
    "ForageCapacity",
    "ForageError",
    "ForageGrant",
    "ForageMap",
    "ForageRequest",
    "ForageRequestKind",
    "GoalBudgets",
    "GpuInfo",
    "GrantInputs",
    "GrantState",
    "HostCapacity",
    "HostingPlan",
    "InvalidGrantTransitionError",
    "LocalPool",
    "ModelCost",
    "ModelSlot",
    "ModelSource",
    "ModelSourceSpec",
    "RoleFootprint",
    "RoyalReserve",
    "Seat",
    "SeatReservation",
    "SlotBinding",
    "SlotPlan",
    "SourceChain",
    "Tempo",
    "UnknownSourceError",
    "assert_transition",
    "can_transition",
    "grade_floor",
    "grant",
    "is_terminal",
]
