"""Define the Honey Store's own value models: Nectar, Honey, and the shapes search passes around.

A concept needing more than one file becomes a package with an `__init__` face (codingrules 5.2);
this one holds the pydantic and dataclass value models `hivemind.honey_store.store` persists and
returns, split by responsibility into `nectar.py` (raw findings), `honey.py` (ripened knowledge)
and `search.py` (the filter and candidate shapes a search passes between the store and its
ranker), so each stays under the codingrules 5.1 size limit.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy), inside the honey_store package. Read
    and written by `hivemind.honey_store.store`; built by `hivemind.honey_store.scope` and
    `.clearance` (Nectar) and by `hivemind.honey_store.ripening` (Honey, a later dispatch).

Key invariants:
    - Every model here is frozen (codingrules 8.5): a state change always produces a new instance
      via `model_copy`, never a mutation.

See Also:
    - .claude/codingrules.md section 5.2 for the package-over-prefixed-siblings rule this follows.
    - hivemind.honey_store.models.nectar, .honey, .search for the definitions.

Public API:
    - Nectar, NectarDraft, NectarOrigin, NectarSource, NectarState (nectar): raw findings, before
      ripening, and a content duplicate's extra provenance (ADR-0037).
    - Honey, HoneyDraft, HoneyPart (honey): ripened, retrievable knowledge.
    - ReadFilter, TextCandidate, VectorCandidate, HoneyStats (search): the shapes a search and
      `HoneyStore.stats` pass around.
"""

from hivemind.honey_store.models.honey import Honey, HoneyDraft, HoneyPart
from hivemind.honey_store.models.nectar import (
    Nectar,
    NectarDraft,
    NectarOrigin,
    NectarSource,
    NectarState,
)
from hivemind.honey_store.models.search import (
    HoneyStats,
    ReadFilter,
    TextCandidate,
    VectorCandidate,
)

__all__ = [
    "Honey",
    "HoneyDraft",
    "HoneyPart",
    "HoneyStats",
    "Nectar",
    "NectarDraft",
    "NectarOrigin",
    "NectarSource",
    "NectarState",
    "ReadFilter",
    "TextCandidate",
    "VectorCandidate",
]
