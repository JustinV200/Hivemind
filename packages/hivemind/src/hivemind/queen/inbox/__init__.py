"""Provide the Queen's Attendant: her inbox triage, human messages heavy but not absolute.

`links.py` hears every attached Warden link at once: one reader task per link feeds its own bounded
queue, the Queen's tick drains everything queued before her Attendant orders it, and the newest
Heartbeat each link delivered is what her liveness sweep judges by, so a stall of her own tick is
never mistaken for a silent Warden. `weights.py` builds the Queen's own
`hivemind.supervision.attendant.Attendant` over `WeightTable.queen_default()` and classifies one
received envelope into the `InboxItem` shape it scores; `tie_breaker.py` is the model-backed
`TieBreaker` (`ModelSlot.ATTENDANT`) the Queen may enable for an exact score tie or an unknown
inbox kind. Unlike `hivemind.wardens.inbox`, this sub-package MAY import `hivemind.llm`: only
anything under an `autopilot/` directory may not (codingrules section 4).

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside the queen package. Handles
    where a Warden's questions, Alarms and task events queue up for Attendant triage before the
    Queen acts. Called by `hivemind.queen.queen.Queen` and `hivemind.queen.attach`.

Key invariants:
    - `queen_attendant` never hard-codes a `TieBreaker`: the caller decides whether one is enabled.
    - At most `LINK_QUEUE_SIZE` envelopes wait per link, and a reader task exists exactly while
      its link is attached (`LinkReaders`' own module docstring).

See Also:
    - .claude/codingrules.md section 3 for where this sub-package sits under queen.
    - .claude/codingrules.md section 8.8 for the Attendant shape this package builds.
    - .claude/roadmap.md phase 3 step 3.20 for the work that first populates it.

Public API (roadmap step 3.20):
    - LinkReaders, LINK_QUEUE_SIZE: one reader task and one bounded queue per attached Warden
      link, drained whole every tick (links).
    - to_inbox_item, queen_attendant: build the Queen's Attendant and wrap one envelope (weights);
      human_inbox_item, HUMAN_PRINCIPAL: wrap one human chat message (weights, roadmap step 10.5).
    - MAX_TIE_REASON_CHARS, ModelTieBreaker: the model-backed tie-break seam (tie_breaker).
"""

from hivemind.queen.inbox.links import LINK_QUEUE_SIZE, LinkReaders
from hivemind.queen.inbox.tie_breaker import MAX_TIE_REASON_CHARS, ModelTieBreaker
from hivemind.queen.inbox.weights import (
    HUMAN_PRINCIPAL,
    human_inbox_item,
    queen_attendant,
    to_inbox_item,
)

__all__ = [
    "HUMAN_PRINCIPAL",
    "LINK_QUEUE_SIZE",
    "MAX_TIE_REASON_CHARS",
    "LinkReaders",
    "ModelTieBreaker",
    "human_inbox_item",
    "queen_attendant",
    "to_inbox_item",
]
