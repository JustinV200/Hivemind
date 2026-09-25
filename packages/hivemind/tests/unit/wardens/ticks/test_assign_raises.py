"""Tests for hivemind.wardens.ticks.assign.handle_grant keeping the audit raises a grant carried.

Roadmap step 10.6 (Waggle 1.10): a Warden whose trail never sees the Queen's (a Virtual Cell's
in-Cell one) learns of a Guard Bee raise of a Capping tier's audit rate from the grants the Queen
issues it. Every grant it records hands its raises to the Warden's one `CarriedAuditRaises`, which
every gate it builds reads; a grant from an older Queen, with none, changes nothing.

Fits into the Hive:
    Mirrors src/hivemind/wardens/ticks/assign.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - tests.unit.queen.forage.test_grant_message for the Queen filling the raises in.
"""

from __future__ import annotations

from datetime import timedelta

from builders.forage import make_grant
from builders.wardens import make_warden_deps

from hivemind.supervision.capping import RiskTier
from hivemind.wardens.ticks.assign import handle_grant
from hivemind.wardens.warden import Warden
from waggle.messages.forage import GrantIssued, RaisedAuditRate

_HOLD = timedelta(hours=1)  # How long every carried raise here lasts.


def _wire(warden: Warden, *raises: RaisedAuditRate) -> GrantIssued:
    """A grant for this Warden carrying `raises`, as the Queen sends one."""
    grant = make_grant(clock=warden._deps.clock)
    return grant.to_wire({}).model_copy(update={"audit_raises": raises})


async def test_every_grant_a_warden_records_hands_on_the_raises_it_carried() -> None:
    deps, _, warden_id = make_warden_deps()
    warden = Warden(warden_id, deps)
    now = deps.clock.now()
    raised = RaisedAuditRate(tier="SCRATCH_WRITE", rate=1.0, until=now + _HOLD)

    await handle_grant(warden, _wire(warden, raised))
    await handle_grant(warden, _wire(warden))  # An older Queen's grant: nothing carried.

    assert deps.carried_raises.rate(RiskTier.SCRATCH_WRITE, now) == 1.0
    assert deps.carried_raises.rate(RiskTier.NETWORK_EGRESS, now) == 0.0
