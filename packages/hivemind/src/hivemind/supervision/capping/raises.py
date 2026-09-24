"""Define a Guard Bee raise of a Capping tier's sampled-audit rate, and every way it is read.

Codingrules section 8.12: "the Guard Bee raises a tier's rate when its failure rate climbs."
`AuditRateRaise` is what a raise carries on the trail (`guard.audit_rate_raised`, roadmap step
10.6: the tier, the rate it rose from and to, until when, and the report behind it). A raise
reaches every Capping gate that samples at it by one of two roads. A Warden whose gate records to
the Queen's own trail (the Hive Stand's) reads it back there, `raised_audit_rate`. Every other
Warden, a Virtual Cell's in-Cell one above all, records to a trail of its own that never sees the
Queen's, so the Queen reads the raises in force when she issues a grant (`live_audit_raises`) and
the grant carries them on the wire (`GrantIssued.audit_raises`, Waggle 1.8); the Warden keeps them
in its `CarriedAuditRaises`, which every gate it builds consults. Either way a proposal is sampled
at the higher of the tier table's rate and the highest raise in force. A raise only ever raises:
the model refuses one that does not, and every reader takes the maximum.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy), inside the supervision package's
    capping sub-package. `AuditRateRaise` is written by the Guard Bee
    (`hivemind.workers.roles.guard_bee`); `raised_audit_rate` and `CarriedAuditRaises` are read
    by `hivemind.wardens.spawn.audited_gate`; `live_audit_raises` by the Queen when she issues a
    grant (`hivemind.queen.forage.grants`); `CarriedAuditRaises.carry` by a Warden receiving one.
    Calls into `hivemind.guard.report` (the report id pattern), `hivemind.pheromone`,
    `.tiers` (RiskTier) and waggle only.

Key invariants:
    - Nothing here ever lowers a rate: every reader returns the highest raise in force, or 0.0.
    - An expired, malformed or unknown-tier raise is skipped, never trusted.

See Also:
    - .claude/codingrules.md section 8.12 for "What cannot be gated is sampled."
    - docs/waggle/spec.md section 8.4 for GrantIssued.audit_raises and RaisedAuditRate.
    - hivemind.supervision.capping.audit for the sampling a raise feeds.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, JsonValue, ValidationError, model_validator

from hivemind.guard.report import GUARD_REPORT_ID_PATTERN
from hivemind.pheromone import PheromoneEvent, PheromoneTrail, TrailQuery
from hivemind.supervision.capping.tiers import RiskTier
from waggle.messages.base import UtcDatetime
from waggle.messages.forage import RaisedAuditRate

AUDIT_RATE_RAISED_KIND = "guard.audit_rate_raised"  # The trail kind a Guard Bee raise is.
# Raises are rare (a rule consumes the burst that fired it): the newest 64 always include each
# tier's newest live raise, which is its highest, since every raise starts from the rate in force.
MAX_RAISES_READ = 64
MAX_CARRIED_PER_TIER = 8  # Raises one Warden keeps per tier; only the highest in force matters.

__all__ = [
    "AUDIT_RATE_RAISED_KIND",
    "MAX_CARRIED_PER_TIER",
    "MAX_RAISES_READ",
    "AuditRateRaise",
    "CarriedAuditRaises",
    "live_audit_raises",
    "raised_audit_rate",
]


class AuditRateRaise(BaseModel):
    """One Guard Bee raise of one tier's sampled-audit rate: the `guard.audit_rate_raised` payload.

    Written by the Guard Bee (roadmap step 10.6) when a rule sees a tier's failures climb, and read
    back from the trail by `raised_audit_rate` and `live_audit_raises`; the trail is its durable
    record, so a raise survives a restart of either side and needs no shared object.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    tier: RiskTier = Field(description="The tier whose sampling rate rose.")
    from_rate: float = Field(ge=0.0, lt=1.0, description="The rate in force before the raise.")
    to_rate: float = Field(gt=0.0, le=1.0, description="The rate in force until `until`.")
    until: UtcDatetime = Field(description="When the raise lapses and the table's rate returns.")
    report_id: str = Field(pattern=GUARD_REPORT_ID_PATTERN, description="The report behind it.")
    rule: str = Field(
        pattern=r"^[a-z][a-z0-9_.]*$", max_length=64, description="The rule that raised it."
    )

    @model_validator(mode="after")
    def _only_ever_raises(self) -> Self:
        """Refuse a 'raise' that does not raise: lowering a rate would widen what goes unaudited."""
        if self.to_rate <= self.from_rate:
            raise ValueError(f"A raise must raise: {self.to_rate} is not above {self.from_rate}.")
        return self

    def to_payload(self) -> dict[str, JsonValue]:
        """Return the raise as a trail payload: a tier name, two rates, a time and two ids."""
        return self.model_dump(mode="json")

    @classmethod
    def from_event(cls, event: PheromoneEvent) -> AuditRateRaise | None:
        """Read a raise back from a `guard.audit_rate_raised` event; None when it is malformed.

        Args:
            event: One trail event of kind `AUDIT_RATE_RAISED_KIND`.

        Returns:
            The raise, or None for a payload this model refuses (a foreign or damaged row, which
            a reader skips rather than trusts).
        """
        try:
            return cls.model_validate(event.payload)
        except ValidationError:
            return None


async def raised_audit_rate(trail: PheromoneTrail, tier: RiskTier, now: datetime) -> float:
    """Return the highest live Guard Bee raise of `tier`'s sampled-audit rate, or 0.0.

    Args:
        trail: The trail the calling gate records to; on the Hive Stand that is the Queen's own
            trail, where the Guard Bee records every raise.
        tier: The tier a terminal proposal is being considered for sampling at.
        now: The reference time; a raise whose `until` has passed is ignored.

    Returns:
        The largest `to_rate` among `tier`'s raises still in force, or 0.0 with none.
    """
    # Latency: one indexed local read of at most MAX_RAISES_READ rows (the kind index), once per
    # terminal proposal, after its outcome is already decided.
    events = await trail.query(
        TrailQuery(kind=AUDIT_RATE_RAISED_KIND, newest_first=True, limit=MAX_RAISES_READ)
    )
    raises = (AuditRateRaise.from_event(event) for event in events)
    live = [r.to_rate for r in raises if r is not None and r.tier is tier and r.until > now]
    return max(live, default=0.0)


async def live_audit_raises(trail: PheromoneTrail, now: datetime) -> tuple[AuditRateRaise, ...]:
    """Return, per tier, the highest Guard Bee raise still in force: what a grant carries.

    Args:
        trail: The Queen's own trail, where the Guard Bee records every raise.
        now: The reference time; a raise whose `until` has passed is not in force.

    Returns:
        At most one raise per tier, the one with the highest `to_rate`, in tier order.
    """
    # Latency: one indexed local read of at most MAX_RAISES_READ rows, once per grant issued.
    events = await trail.query(
        TrailQuery(kind=AUDIT_RATE_RAISED_KIND, newest_first=True, limit=MAX_RAISES_READ)
    )
    best: dict[RiskTier, AuditRateRaise] = {}
    for raised in (AuditRateRaise.from_event(event) for event in events):
        if raised is None or raised.until <= now:
            continue  # Malformed or lapsed: never carried.
        known = best.get(raised.tier)
        if known is None or raised.to_rate > known.to_rate:
            best[raised.tier] = raised
    return tuple(best[tier] for tier in RiskTier if tier in best)


class CarriedAuditRaises:
    """The raises the Queen carried to one Warden on its grants; every gate it builds reads them.

    Owns its own mutable state (codingrules 8.5): `carry` is the only write. One per Warden, like
    `AuditRates`, shared by every gate that Warden builds, so a raise carried on any grant reaches
    every sub-bee's gate from its next terminal proposal on.
    """

    def __init__(self) -> None:
        """Hold nothing yet: until a grant carries a raise, the tier table's rates stand."""
        self._carried: dict[RiskTier, list[tuple[float, datetime]]] = {}

    def carry(self, raises: Iterable[RaisedAuditRate], now: datetime) -> None:
        """Keep the raises one grant carried, beside those earlier grants did.

        Args:
            raises: `GrantIssued.audit_raises`.
            now: The receiving Warden's time; a raise already past is dropped.
        """
        for raised in raises:
            try:
                tier = RiskTier(raised.tier)
            except ValueError:
                continue  # A tier this Warden does not know (a newer Queen's): ignored, by spec.
            self._carried.setdefault(tier, []).append((raised.rate, raised.until))
        for tier, kept in self._carried.items():
            live = sorted((entry for entry in kept if entry[1] > now), reverse=True)
            self._carried[tier] = live[:MAX_CARRIED_PER_TIER]

    def rate(self, tier: RiskTier, now: datetime) -> float:
        """Return the highest carried raise of `tier` in force at `now`, or 0.0 with none.

        Args:
            tier: The tier a terminal proposal is being considered for sampling at.
            now: The reference time.

        Returns:
            The largest carried rate whose raise has not lapsed.
        """
        live = [rate for rate, until in self._carried.get(tier, ()) if until > now]
        return max(live, default=0.0)
