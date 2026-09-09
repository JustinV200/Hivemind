"""Queue questions, Alarms and Worker results for the Warden's Attendant triage.

Questions, Alarms and results from its Workers queue up here for Attendant, the inbox triage
every supervisor uses. `warden_attendant` builds a Warden's own `Attendant` over `WeightTable.
warden_default()`, autopilot-only (no `TieBreaker`, codingrules section 8.8); `to_inbox_item`
classifies one received `waggle.envelope.Envelope`'s payload into the `InboxItem` shape that
Attendant scores.

Fits into the Hive:
    Layer 5 (per-Cell supervisors; spawn and supervise Workers), inside the wardens package.
    Handles where questions, Alarms and results from its Workers queue up. Called by
    `hivemind.wardens.warden.Warden`.

Key invariants:
    - `warden_attendant` never passes a `TieBreaker`: a Warden's Attendant is autopilot-only by
      default.

See Also:
    - .claude/codingrules.md section 3 for where this sub-package sits under wardens.
    - .claude/codingrules.md section 8.8 for the Attendant shape this package builds.
    - .claude/roadmap.md phase 3 step 3.19 for the work that first populates it.

Public API (roadmap step 3.19):
    - to_inbox_item, warden_attendant: build the Warden's Attendant and wrap one envelope (weights).
"""

from hivemind.wardens.inbox.weights import to_inbox_item, warden_attendant

__all__ = ["to_inbox_item", "warden_attendant"]
