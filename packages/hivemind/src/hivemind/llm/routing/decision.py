"""Define RoutingDecision and the data it carries: which binding, why, and what was passed over.

Routing (picking which binding of a slot's fallback chain a bee calls first) answers with a
``RoutingDecision``. It never invents a binding: ``key`` is one of the slot's own ``[llm.slots]``
keys and ``order`` is the same chain reordered with the chosen key first, so the caller rebinds
along ``order`` and the Fanner (the seat meter every model call passes through) still spills down
it at call time. Everything the decision rests on is data rather than prose, in the shape of
``hivemind.queen.forage.hosting.PlanReason``: a ``RoutingReason`` (the floor applied, how many
candidates were eligible, whether the choice is local, planned or a fallback), the candidates
``passed_over`` and the ``PassOverCause`` for each, and a ``ForageShortfall`` when the chosen
source has no free seat, so a Warden (the per-Cell supervisor) can file a ``ForageRequest`` for
the seat instead of waiting in the Fanner's queue unnoticed. ``RoutingReason.render`` turns the
data into the bounded string a trail event (the Pheromone Trail's audit record) carries.

Fits into the Hive:
    Layer 1 (foundational services; capacity as data), inside ``hivemind.llm.routing``. Built by
    ``hivemind.llm.routing.policy.choose_binding``; read by the Warden and Queen code that spawns
    or rebinds a bee and records ``llm.fallback``. Calls into ``hivemind.forage`` (``Effort``)
    and ``waggle`` (the reason bound) only.

Key invariants:
    - ``order`` is a permutation of the slot's fallback chain and ``order[0] == key``.
    - ``passed_over`` never names ``key`` unless the decision fell back to the slot's own primary
      because every candidate was ineligible (``reason.fell_back``): the primary was judged and
      failed, and the reason says so.
    - ``forage_shortfall`` is None unless the chosen source is eligible, on the Forage map and
      has no free seat; an unknown source has no seat count to be short of, and a fallback to the
      slot's primary (nothing eligible) is not a reason to ask for seats on it.
    - ``RoutingReason.render`` never exceeds ``MAX_REASON_CHARS``.

See Also:
    - .claude/roadmap.md step 8.1 for "reports why, what it passed over, and a Forage shortfall".
    - hivemind.queen.forage.hosting.PlanReason for the reason-as-data shape this follows.
    - hivemind.llm.routing.policy for the function that builds one.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from hivemind.forage import Effort
from waggle.messages.base import MAX_REASON_CHARS

__all__ = ["ForageShortfall", "PassOverCause", "PassedOver", "RoutingDecision", "RoutingReason"]


class PassOverCause(Enum):
    """Why routing did not choose a binding of the chain (it may still be a fallback)."""

    GRADE_BELOW_FLOOR = "GRADE_BELOW_FLOOR"  # Ineligible: weaker than the slot's floor allows.
    CAPABILITY_MISSING = "CAPABILITY_MISSING"  # Ineligible: lacks a capability the task needs.
    TOO_FAR = "TOO_FAR"  # Ineligible: measured latency is past the task's latency budget.
    OVER_COST = "OVER_COST"  # Ineligible: it spends money and the goal's spend cap is zero.
    NOT_LOCAL_OFFLINE = "NOT_LOCAL_OFFLINE"  # Ineligible: the Hive is offline, the model is not.
    NO_SEAT = "NO_SEAT"  # Eligible, but zero free seats, so it ranks behind seated peers.
    NOT_ON_MAP = "NOT_ON_MAP"  # Eligible, but unknown to Forage, so it ranks behind known peers.


@dataclass(frozen=True, slots=True)
class PassedOver:
    """One chain binding routing did not choose, and the single reason it names for it."""

    key: str  # The `[llm.slots]` key that was passed over.
    cause: PassOverCause  # The first reason that applied, in the order eligibility checks run.


@dataclass(frozen=True, slots=True)
class ForageShortfall:
    """Seats a bee wants on a source that has none free, so a caller can file a ForageRequest."""

    source_id: str  # The Forage map source that has no free seat.
    seats_wanted: int  # How many seats the bee needs there; one, for a single bee.


@dataclass(frozen=True, slots=True)
class RoutingReason:
    """The factors that decided a routing choice, as data (the shape of ``PlanReason``).

    Attributes:
        floor: The grade floor applied after tempo, the raised floor and the map-top cap.
        considered: How many bindings the slot's chain holds.
        eligible: How many of them cleared every check.
        chosen_grade: The chosen source's effective grade; None for an unknown source.
        local: True when the chosen source runs on the Cell the bee runs on.
        planned: True when the chosen source is in the Cell's hosting plan chain for the slot.
        fell_back: True when no binding was eligible and the slot's own primary was chosen.
    """

    floor: int
    considered: int
    eligible: int
    chosen_grade: int | None
    local: bool
    planned: bool
    fell_back: bool

    def render(self) -> str:
        """Render this reason as the bounded string a ``llm.fallback`` event carries."""
        grade = "unknown" if self.chosen_grade is None else str(self.chosen_grade)
        text = (
            f"{self.eligible} of {self.considered} binding(s) eligible at floor {self.floor}; "
            f"chosen grade={grade}; local={self.local}; planned={self.planned}; "
            f"fell_back_to_primary={self.fell_back}."
        )
        return text[:MAX_REASON_CHARS]


@dataclass(frozen=True, slots=True)
class RoutingDecision:
    """Which binding of a slot's chain to call first, at what effort, and why."""

    key: str  # The chosen `[llm.slots]` key; always one of the slot's own chain.
    order: tuple[str, ...]  # The whole chain, chosen first, then the rest in rank order.
    effort: Effort  # How hard to ask the chosen model to think, from tempo and the row's own.
    reason: RoutingReason  # The deciding factors, as data.
    passed_over: tuple[PassedOver, ...]  # Bindings not chosen, each with its cause.
    forage_shortfall: ForageShortfall | None  # Set when the chosen source has no free seat.
