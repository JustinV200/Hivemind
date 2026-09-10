"""Provide the Queen's Attendant: her inbox triage, human messages heavy but not absolute.

`weights.py` builds the Queen's own `hivemind.supervision.attendant.Attendant` over
`WeightTable.queen_default()` and classifies one received envelope into the `InboxItem` shape it
scores; `tie_breaker.py` is the model-backed `TieBreaker` (`ModelSlot.ATTENDANT`) the Queen may
enable for an exact score tie or an unknown inbox kind. Unlike `hivemind.wardens.inbox`, this
sub-package MAY import `hivemind.llm`: only anything under an `autopilot/` directory may not
(codingrules section 4).

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside the queen package. Handles
    where a Warden's questions, Alarms and task events queue up for Attendant triage before the
    Queen acts. Called by `hivemind.queen.queen.Queen`.

Key invariants:
    - `queen_attendant` never hard-codes a `TieBreaker`: the caller decides whether one is enabled.

See Also:
    - .claude/codingrules.md section 3 for where this sub-package sits under queen.
    - .claude/codingrules.md section 8.8 for the Attendant shape this package builds.
    - .claude/roadmap.md phase 3 step 3.20 for the work that first populates it.

Public API (roadmap step 3.20):
    - to_inbox_item, queen_attendant: build the Queen's Attendant and wrap one envelope (weights).
    - MAX_TIE_REASON_CHARS, ModelTieBreaker: the model-backed tie-break seam (tie_breaker).
"""

from hivemind.queen.inbox.tie_breaker import MAX_TIE_REASON_CHARS, ModelTieBreaker
from hivemind.queen.inbox.weights import queen_attendant, to_inbox_item

__all__ = ["MAX_TIE_REASON_CHARS", "ModelTieBreaker", "queen_attendant", "to_inbox_item"]
