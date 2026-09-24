"""Build Guard Bees over fakes, and seed the trails their rules count.

`make_guard_bee` builds a Guard Bee over an in-memory trail, a FakeClock, a recording Queen door,
an in-memory C2 sink and a scriptable judge slot; `TrailSeeder` records the events the rules
count as their real producers write them; `SHIPPED_RULE_SEEDERS` seeds, per shipped rule, a trail
it fires on or one it just stays quiet on.

Fits into the Hive:
    Test infrastructure (codingrules section 14.5), not shipped.

Key invariants:
    - None: this face re-exports only.

See Also:
    - hivemind.workers.roles.guard_bee for what these build.
"""

from builders.guard_bee.rig import (
    GuardBeeRig,
    RecordingDoor,
    RigOptions,
    judge_reply,
    make_guard_bee,
    restart_guard_bee,
)
from builders.guard_bee.seeds import Episode, TrailSeeder, seed_episode
from builders.guard_bee.shipped import SHIPPED_RULE_SEEDERS, Seeder

__all__ = [
    "SHIPPED_RULE_SEEDERS",
    "Episode",
    "GuardBeeRig",
    "RecordingDoor",
    "RigOptions",
    "Seeder",
    "TrailSeeder",
    "judge_reply",
    "make_guard_bee",
    "restart_guard_bee",
    "seed_episode",
]
