"""Define ForageAutopilotOutcome, ForageRequestSignal and decide_forage_request.

Roadmap step 4.7: "`ForageRequest` handled by an autopilot rule within headroom, by awake when
contested." This is that rule: a deterministic table over one already-computed signal, exactly the
shape codingrules section 8.8 asks every autopilot decision to have ("a deterministic dispatch
table over (event kind, state)... before any model is consulted"). "Contested" means the request
cannot be met from the shared pool's current headroom alone, but could be if another live grant
were shrunk first -- a judgement call about whose need is greater, which this table deliberately
does not make; it only recognises that the call exists and hands it to `hivemind.queen.awake`
(`NEEDS_JUDGEMENT`) instead of guessing. `ForageRequestSignal` groups the two booleans this table
reads: whether `hivemind.queen.forage.ledger.ForageLedger.headroom` alone already covers the ask,
and whether some other live grant could be shrunk to cover the rest -- both computed by the caller
(`hivemind.queen.forage.requests`, outside this `autopilot/` directory) from the ledger, since this
module may touch no I/O and no state of its own (codingrules section 4: nothing under `autopilot/`
may import `hivemind.llm`, directly or transitively, and this table goes further still and reads
nothing beyond its own argument).

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside the queen package's
    autopilot sub-package (which never imports `hivemind.llm`). Called by
    `hivemind.queen.ticks.forage` once per received `ForageRequest`. Calls into nothing beyond the
    standard library.

Key invariants:
    - This module imports nothing beyond `dataclasses` and `enum`: it can never import
      `hivemind.llm`, directly or transitively (codingrules section 4; `lint-imports` enforces it
      for every module under an `autopilot/` directory).
    - `decide_forage_request` is pure: the same `ForageRequestSignal` always returns the same
      `ForageAutopilotOutcome`.

See Also:
    - .claude/codingrules.md section 8.8 for the autopilot-first-awake-second shape this table
      implements.
    - .claude/codingrules.md section 8.14 for "the Queen tunes her own effort... contested Forage
      at high" (Effort.HIGH is `hivemind.queen.ticks.forage`'s own choice for the NEEDS_JUDGEMENT
      case, not this module's; see that module's docstring for what queen.awake still lacks).
    - hivemind.queen.forage.requests for the one caller that builds a ForageRequestSignal from the
      ledger and acts on this table's own outcome.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

__all__ = ["ForageAutopilotOutcome", "ForageRequestSignal", "decide_forage_request"]


class ForageAutopilotOutcome(Enum):
    """What autopilot decides about one ForageRequest, before any awake episode runs."""

    GRANT = "GRANT"  # Met from headroom alone; hivemind.queen.forage.grants issues it at once.
    DENY = "DENY"  # Cannot be met even by shrinking another live grant; forage.denied, with why.
    NEEDS_JUDGEMENT = "NEEDS_JUDGEMENT"  # Contested: hand off to queen.awake.


@dataclass(frozen=True, slots=True)
class ForageRequestSignal:
    """The two booleans `decide_forage_request` reads, precomputed by the caller from the ledger.

    Attributes:
        within_headroom: True when `hivemind.queen.forage.ledger.ForageLedger.headroom` alone
            already covers what the request asks for.
        shrinkable: True when some other live grant could be shrunk to cover the rest; read only
            when `within_headroom` is False.
    """

    within_headroom: bool
    shrinkable: bool


def decide_forage_request(signal: ForageRequestSignal) -> ForageAutopilotOutcome:
    """Return autopilot's verdict on one ForageRequest, from an already-computed signal.

    Args:
        signal: Whether headroom alone covers the ask, and whether shrinking another live grant
            could cover the rest.

    Returns:
        GRANT when headroom alone covers it; NEEDS_JUDGEMENT when it does not but shrinking
        another grant could; DENY when neither can.
    """
    if signal.within_headroom:
        return ForageAutopilotOutcome.GRANT
    if signal.shrinkable:
        return ForageAutopilotOutcome.NEEDS_JUDGEMENT
    return ForageAutopilotOutcome.DENY
