"""Seed, for each shipped Guard Bee rule, a trail it fires on, or one it just stays quiet on.

`SHIPPED_RULE_SEEDERS` maps every shipped rule's key to a seeder that records, on a rig's trail,
the events that rule's real producers write: once enough to reach its threshold (`fire=True`) and
once one short of it (`fire=False`), or, for a threshold of one, the same evidence out of its
window. The shipped-rule tests run each alone; the Guard Bee's whole-round tests run them all at
once, so a request, a raise and a reduction all happen on one trail.

Fits into the Hive:
    Test infrastructure (codingrules section 14.5), not shipped. Used through `builders.guard_bee`.

Key invariants:
    - There is a seeder for every shipped rule and for nothing else (the shipped-rule test checks).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from builders.cells import make_identity
from builders.guard_bee.rig import GuardBeeRig
from builders.guard_bee.seeds import TrailSeeder, bind_cell, cell_episode, seed_episode

from waggle.ids import new_cell_id, new_node_id

__all__ = ["SHIPPED_RULE_SEEDERS", "Seeder"]

Seeder = Callable[[GuardBeeRig, bool], Awaitable[None]]
_TIER = "SCRATCH_WRITE"  # The tier every Capping seed here proposes at.
_HOUR_AND_A_SECOND = 3_601.0  # Past every one-hour window: a lone refusal has aged out of it.


async def _repeat(times: int, act: Callable[[], Awaitable[object]]) -> None:
    """Run `act` `times` times, in order."""
    for _ in range(times):
        await act()


async def _injection_then_denial(rig: GuardBeeRig, fire: bool) -> None:
    episode = await seed_episode(rig.seed)
    await rig.seed.injection(episode.bee, episode.task)
    # Just under: the flag alone, with no refusal after it in the episode.
    if fire:
        await rig.seed.denied(episode.bee)


async def _injection_burst(rig: GuardBeeRig, fire: bool) -> None:
    episode = await seed_episode(rig.seed)
    await _repeat(3 if fire else 2, lambda: rig.seed.injection(episode.bee, episode.task))


async def _denial_burst(rig: GuardBeeRig, fire: bool) -> None:
    episode = await seed_episode(rig.seed)
    await _repeat(5 if fire else 4, lambda: rig.seed.denied(episode.bee))


async def _network_attempts(rig: GuardBeeRig, fire: bool) -> None:
    episode = await seed_episode(rig.seed)
    await _repeat(3 if fire else 2, lambda: rig.seed.denied(episode.bee, "net:example.org"))


async def _outside_scratch_touches(rig: GuardBeeRig, fire: bool) -> None:
    episode = await seed_episode(rig.seed)
    await _repeat(25 if fire else 24, lambda: rig.seed.touched(episode.cell))


async def _outside_scratch_refusals(rig: GuardBeeRig, fire: bool) -> None:
    episode = await seed_episode(rig.seed)
    for _ in range(3 if fire else 2):
        proposal = await rig.seed.proposed(episode.task, episode.cell, "OUTSIDE_SCRATCH_WRITE")
        await rig.seed.capping(
            proposal, "capping.rejected", tier="OUTSIDE_SCRATCH_WRITE", failing_check="ALLOWLIST"
        )


async def _over_grant(rig: GuardBeeRig, fire: bool) -> None:
    episode = await seed_episode(rig.seed)
    await _repeat(3 if fire else 2, lambda: rig.seed.alarm(episode.task))


async def _grant_spend_surge(rig: GuardBeeRig, fire: bool) -> None:
    episode = await seed_episode(rig.seed)
    await rig.seed.llm_call(episode.grant, 2.5)
    await rig.seed.llm_call(episode.grant, 2.5 if fire else 2.4)


async def _proposals(rig: GuardBeeRig, count: int) -> list[str]:
    """`count` proposals at `_TIER` from one fresh episode; their ids."""
    episode = await seed_episode(rig.seed)
    return [await rig.seed.proposed(episode.task, episode.cell, _TIER) for _ in range(count)]


async def _capping_rejection_rate(rig: GuardBeeRig, fire: bool) -> None:
    proposals = await _proposals(rig, 10)
    for proposal in proposals[: 5 if fire else 4]:
        await rig.seed.capping(proposal, "capping.rejected", tier=_TIER, failing_check="SCHEMA")


async def _capping_rollback_rate(rig: GuardBeeRig, fire: bool) -> None:
    proposals = await _proposals(rig, 5)
    for proposal in proposals:
        await rig.seed.capping(proposal, "capping.applied", action_kind="DIFF")
    for proposal in proposals[: 2 if fire else 1]:
        await rig.seed.capping(proposal, "capping.rolled_back", method="REVERSE_DIFF")


async def _audit_failure_rate(rig: GuardBeeRig, fire: bool) -> None:
    outcomes = ["REJECT", "APPROVE", "APPROVE", "APPROVE"] + ([] if fire else ["APPROVE"])
    for proposal, outcome in zip(await _proposals(rig, len(outcomes)), outcomes, strict=True):
        await rig.seed.capping(proposal, "capping.audited", tier=_TIER, outcome=outcome)


async def _refused_once(rig: GuardBeeRig, fire: bool, kind: str, reason: str) -> None:
    """One Cell gate refusal of `kind` for a fresh Cell; just under, it has left its window."""
    await rig.seed.refused(kind, new_cell_id(rig.clock), reason)
    if not fire:
        rig.clock.advance(_HOUR_AND_A_SECOND)


def _node_seeder(rig: GuardBeeRig) -> tuple[str, TrailSeeder]:
    """A fresh Virtual Cell node recording onto the rig's trail, as its shipped segment does."""
    node = new_node_id(rig.clock)
    identity = make_identity(rig.clock, hive_id=rig.identity.hive_id, node_id=node)
    return node, TrailSeeder(rig.trail, rig.clock, identity)


async def _subject_forgery(rig: GuardBeeRig, fire: bool) -> None:
    victim_node, victim_seed = _node_seeder(rig)
    victim = await cell_episode(rig.seed, victim_seed, await bind_cell(rig.seed, victim_node))
    framer_node, framer_seed = _node_seeder(rig)
    await bind_cell(rig.seed, framer_node)
    await framer_seed.denied(victim.bee)  # The framer's node names the victim's bee.
    # Just under: the one forged record has left its hour's window.
    if not fire:
        rig.clock.advance(_HOUR_AND_A_SECOND)


async def _envelope_forgery(rig: GuardBeeRig, fire: bool) -> None:
    await _refused_once(rig, fire, "guard.envelope_refused", "invalid")


async def _segment_forgery(rig: GuardBeeRig, fire: bool) -> None:
    await _refused_once(rig, fire, "guard.segment_refused", "another_node")


async def _segment_unmergeable(rig: GuardBeeRig, fire: bool) -> None:
    await _refused_once(rig, fire, "guard.segment_refused", "corrupt")


async def _request_forgery(rig: GuardBeeRig, fire: bool) -> None:
    for reason in ["request_replay", "request_signature"][: 2 if fire else 1]:
        await rig.seed.entrance("guard.entrance_login_failed", reason=reason, listener="remote")


async def _login_failure_burst(rig: GuardBeeRig, fire: bool) -> None:
    for _ in range(20 if fire else 19):
        await rig.seed.entrance(
            "guard.entrance_login_failed", reason="password", listener="remote", step="login"
        )


async def _lockouts_across_devices(rig: GuardBeeRig, fire: bool) -> None:
    device = await rig.seed.entrance("guard.entrance_locked", reason="lockout")
    # Just under: the same device locked again is still one device.
    await rig.seed.entrance("guard.entrance_locked", None if fire else device, reason="lockout")


async def _invite_abuse(rig: GuardBeeRig, fire: bool) -> None:
    for _ in range(5 if fire else 4):
        await rig.seed.entrance(
            "guard.entrance_redeem_failed",
            rig.identity.hive_id,
            reason="unknown_code",
            address="203.0.113.9",
        )


async def _travel_lock_triggered(rig: GuardBeeRig, fire: bool) -> None:
    await rig.seed.entrance("guard.entrance_travel_lock", network="198.51.100.0/24")
    # Just under: the one trigger has left its hour's window.
    if not fire:
        rig.clock.advance(3_601.0)


# Keyed by rule key, in the order rules.toml ships them.
SHIPPED_RULE_SEEDERS: dict[str, Seeder] = {
    "injection_then_denial": _injection_then_denial,
    "injection_burst": _injection_burst,
    "denial_burst": _denial_burst,
    "network_attempts": _network_attempts,
    "outside_scratch_touches": _outside_scratch_touches,
    "outside_scratch_refusals": _outside_scratch_refusals,
    "over_grant": _over_grant,
    "grant_spend_surge": _grant_spend_surge,
    "capping_rejection_rate": _capping_rejection_rate,
    "capping_rollback_rate": _capping_rollback_rate,
    "audit_failure_rate": _audit_failure_rate,
    "subject_forgery": _subject_forgery,
    "envelope_forgery": _envelope_forgery,
    "segment_forgery": _segment_forgery,
    "segment_unmergeable": _segment_unmergeable,
    "request_forgery": _request_forgery,
    "login_failure_burst": _login_failure_burst,
    "lockouts_across_devices": _lockouts_across_devices,
    "invite_abuse": _invite_abuse,
    "travel_lock_triggered": _travel_lock_triggered,
}
