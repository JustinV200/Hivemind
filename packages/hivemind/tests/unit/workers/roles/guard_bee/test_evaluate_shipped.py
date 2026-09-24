"""Tests for the Guard Bee's shipped rules, one by one: each fires, and stays quiet just under.

Every shipped rule gets a trail seeded with the events its real producers write (the Enforcer's
guard.denied, the scanner's flags, the Capping gate's steps, the Entrance's refusals), once enough
to reach its threshold and once one short of it (or, for a threshold of one, the same evidence out
of its window or out of order). The Guard Bee runs its round over it; a rule that fired left one
`guard.alert` naming it, and one that did not left none. A rule that asks for judgement is judged
by a judge that cannot answer here, so its own verdict stands.

Fits into the Hive:
    Mirrors src/hivemind/workers/roles/guard_bee/evaluate.py (codingrules section 3), split by
    feature from test_evaluate.py.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.workers.roles.guard_bee.rules.toml for the rules under test.
    - builders.guard_bee.shipped for the trail each is seeded with.
    - docs/guard/guard-bee.md for what each counts.
"""

from __future__ import annotations

import pytest
from builders.guard_bee import SHIPPED_RULE_SEEDERS, GuardBeeRig, make_guard_bee

from hivemind.pheromone import PheromoneEvent
from hivemind.workers.roles.guard_bee import load_guard_rules


async def _alerts_for(rig: GuardBeeRig, key: str) -> list[PheromoneEvent]:
    await rig.bee.tick()
    await rig.bee.flush()  # A judged rule's episode ends here; nothing is scripted to answer.
    return [alert for alert in await rig.alerts() if alert.payload["rule"] == key]


def test_every_shipped_rule_has_a_seeded_trail_here() -> None:
    assert set(SHIPPED_RULE_SEEDERS) == {rule.key for rule in load_guard_rules().rules}


@pytest.mark.parametrize("key", sorted(SHIPPED_RULE_SEEDERS))
async def test_a_shipped_rule_fires_once_its_threshold_is_reached(key: str) -> None:
    rig = make_guard_bee()
    await SHIPPED_RULE_SEEDERS[key](rig, True)

    alerts = await _alerts_for(rig, key)

    assert len(alerts) == 1


@pytest.mark.parametrize("key", sorted(SHIPPED_RULE_SEEDERS))
async def test_a_shipped_rule_stays_quiet_just_under_its_threshold(key: str) -> None:
    rig = make_guard_bee()
    await SHIPPED_RULE_SEEDERS[key](rig, False)

    alerts = await _alerts_for(rig, key)

    assert alerts == []
