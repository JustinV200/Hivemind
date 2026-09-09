"""Model capacity as data, including the ModelSlot and Tempo types llm depends on: forage.

HostCapacity, a Seat, RoleFootprint, ForageGrant and ForageRequest, the Forage map of every
source that can serve a model, and the ModelSlot (a named place in a routing ladder a model call
can resolve to) and Tempo (the speed-versus-accuracy dial for a slot) types all live here. It
never imports llm, so a grant or a routing input can name a model slot without pulling in the
provider machinery. Phase 2 step 2.3a built Tempo and its AccuracyBar first, and phase 3 step 3.4
adds ModelSlot and Effort, ahead of the capacity types (phase 3 step 3.12), because
hivemind.cell.needs.TaskNeeds carries a Tempo, hivemind.llm names slots, and all are pure data
with no dependency on the rest of this package.

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
    - HostCapacity, Seat, RoleFootprint, ForageGrant, ForageRequest and the Forage map are not
      implemented yet; they land in phase 3 step 3.12 (roadmap).

See Also:
    - .claude/codingrules.md section 4 for the layer 1 row this package occupies.
    - .claude/codingrules.md section 8.14 for Tempo's role in routing, Forage and Capping.
    - .claude/roadmap.md phase 2 step 2.3a and phase 3 steps 3.4 and 3.12 for the work that
      populates this package.

Public API:
    - AccuracyBar, Tempo: a task's speed-against-accuracy setting (hivemind.forage.tempo).
    - ModelSlot, Effort: the named place a model call resolves to, and how hard it tries
      (hivemind.forage.slots).
"""

from hivemind.forage.slots import Effort, ModelSlot
from hivemind.forage.tempo import AccuracyBar, Tempo

__all__ = ["AccuracyBar", "Effort", "ModelSlot", "Tempo"]
