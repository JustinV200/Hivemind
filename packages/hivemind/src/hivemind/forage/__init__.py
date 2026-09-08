"""Model capacity as data, including the ModelSlot and Tempo types llm depends on: forage.

HostCapacity, a Seat, RoleFootprint, ForageGrant and ForageRequest, the Forage map of every
source that can serve a model, and the ModelSlot (a named place in a routing ladder a model call
can resolve to) and Tempo (the speed-versus-accuracy dial for a slot) types all live here. It
never imports llm, so a grant or a routing input can name a model slot without pulling in the
provider machinery. Phase 2 step 2.3a builds Tempo and its AccuracyBar first, ahead of the
capacity types (phase 4), because hivemind.cell.needs.TaskNeeds carries a Tempo and both are
pure data with no dependency on the rest of this package.

Fits into the Hive:
    Layer 1 (foundational services; capacity as data). Called by queen when it divides capacity
    and llm when it resolves a slot. Calls into hivemind.common and waggle only; it never
    imports hivemind.llm.

Key invariants:
    - AccuracyBar mirrors waggle.messages.labels.AccuracyBar member for member
      (tests/unit/forage/test_tempo.py checks it).
    - Tempo.latency_budget_s is either None or a number strictly greater than zero; never zero or
      negative.
    - HostCapacity, Seat, RoleFootprint, ForageGrant, ForageRequest, the Forage map and
      ModelSlot are not implemented yet; they land in phase 4 (roadmap).

See Also:
    - .claude/codingrules.md section 4 for the layer 1 row this package occupies.
    - .claude/codingrules.md section 8.14 for Tempo's role in routing, Forage and Capping.
    - .claude/roadmap.md phase 2 step 2.3a for the work that first populates this package, and
      phase 4 for the rest.

Public API:
    - AccuracyBar, Tempo: a task's speed-against-accuracy setting (hivemind.forage.tempo).
"""

from hivemind.forage.tempo import AccuracyBar, Tempo

__all__ = ["AccuracyBar", "Tempo"]
