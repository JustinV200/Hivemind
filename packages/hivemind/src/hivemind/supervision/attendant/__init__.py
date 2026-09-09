"""Re-export the Attendant: one supervisor's deterministic inbox triage.

Split into `items.py` (the one InboxItem shape), `weights.py` (WeightTable and Priority) and
`scoring.py` (the pure scorer, the TieBreaker seam and the Attendant class) because together they
exceed codingrules 5.1's 300-line file limit; this face lets a caller write
`from hivemind.supervision.attendant import Attendant` without knowing the split (codingrules 5.2).

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy). Used by `queen/inbox/` and
    `wardens/inbox/` (roadmap steps 3.19 and 3.20). Calls into its own `items`, `weights` and
    `scoring` modules only.

Key invariants:
    - Whatever is not re-exported here is private to this package (codingrules 5.4).

See Also:
    - .claude/codingrules.md section 6.1, "Attendant" row.
    - hivemind.supervision.attendant.items, .weights and .scoring for the definitions.

Public API:
    - InboxKind, InboxItem: the one shape every supervisor's inbox holds (items).
    - WeightTable, Priority: the scoring configuration and one item's scored outcome (weights).
    - TieBreaker, Attendant, score_item: the tie-break seam, the scorer, and its pure core
      (scoring).
"""

from hivemind.supervision.attendant.items import InboxItem, InboxKind
from hivemind.supervision.attendant.scoring import Attendant, TieBreaker, score_item
from hivemind.supervision.attendant.weights import Priority, WeightTable

__all__ = [
    "Attendant",
    "InboxItem",
    "InboxKind",
    "Priority",
    "TieBreaker",
    "WeightTable",
    "score_item",
]
