"""Build Guard Bees over fakes, and seed the trails their rules count.

`make_guard_bee` builds a Guard Bee over an in-memory trail, a FakeClock, a recording Queen door,
an in-memory C2 sink and a scriptable judge slot; `TrailSeeder` records the events the rules
count as their real producers write them; `SHIPPED_RULE_SEEDERS` seeds, per shipped rule, a trail
it fires on or one it just stays quiet on; `guard_bee_for_queen` builds the one a composition
root would build for a running Queen, from her own parts; `quick_rounds` and `GuardReviews` run
the composed Hive's own Guard Bee in a test; `lure_call` and `lure_script` drive a real Drone into
the injection correlation, and `LuredCells`, `LingeringCells` and `ship_on_every_call` run real
in-Cell Wardens whose records reach the Queen at once.

Fits into the Hive:
    Test infrastructure (codingrules section 14.5), not shipped.

Key invariants:
    - None: this face re-exports only.

See Also:
    - hivemind.workers.roles.guard_bee for what these build.
"""

from builders.guard_bee.containers import (
    LingeringCells,
    LuredCells,
    linger_script,
    ship_on_every_call,
)
from builders.guard_bee.hive import GUARD_REVIEW_TITLE, GuardReviews, quick_rounds
from builders.guard_bee.lure import INJECTED, LINGER_S, LURE_CALLS, lure_call, lure_script
from builders.guard_bee.queen import guard_bee_for_queen
from builders.guard_bee.rig import (
    GuardBeeRig,
    RecordingDoor,
    RigOptions,
    judge_reply,
    make_guard_bee,
    restart_guard_bee,
)
from builders.guard_bee.seeds import (
    Episode,
    TrailSeeder,
    bind_cell,
    cell_episode,
    seed_episode,
    spawn,
)
from builders.guard_bee.shipped import SHIPPED_RULE_SEEDERS, Seeder

__all__ = [
    "GUARD_REVIEW_TITLE",
    "INJECTED",
    "LINGER_S",
    "LURE_CALLS",
    "SHIPPED_RULE_SEEDERS",
    "Episode",
    "GuardBeeRig",
    "GuardReviews",
    "LingeringCells",
    "LuredCells",
    "RecordingDoor",
    "RigOptions",
    "Seeder",
    "TrailSeeder",
    "bind_cell",
    "cell_episode",
    "guard_bee_for_queen",
    "judge_reply",
    "linger_script",
    "lure_call",
    "lure_script",
    "make_guard_bee",
    "quick_rounds",
    "restart_guard_bee",
    "seed_episode",
    "ship_on_every_call",
    "spawn",
]
