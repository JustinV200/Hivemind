"""Run a whole Hive with Night Veil Cells over a fake Tor, and read everything it leaves behind.

Codingrules section 12 end to end: `tests.e2e.test_night_veil_boundary` runs one Night Veil goal
through a whole Hive for each way a Night Veil Cell can end, and checks that nothing of the Cell
outlives it but its skeleton, on the Queen's trail or in any store beside it. This package is that
suite's rig, split by responsibility (codingrules 5.2): `rig` builds and runs the Hive (a
container-spawning fake backend, real in-Cell Wardens, a fake Tor, a Worker that can be held at
work), `reads` reads the trail and the chamber by id, and `stores` seeds and reads the Hive's
other stores, a Docker daemon's snapshot images among them.

Fits into the Hive:
    Test infrastructure (codingrules section 14.5), not shipped. Used by
    tests.e2e.test_night_veil_boundary.

Key invariants:
    - This file holds re-exports and `__all__` only.

See Also:
    - hivemind.pheromone.retention for the boundary under test.
    - tests.e2e.test_night_veil_link for the same Hive's Tor link.

Public API:
    - NightVeilRun, night_veil_hive, running, watch_teardown, request, abscond, green_probe,
      red_then_green, EVERYTHING, ProbeFactory, Snapshots, TeardownHook: the Hive (rig).
    - tasks, succeeded, durable, counted, working, night_veil_cells, world, leaks, kinds_about,
      PURGED: the trail and the chamber, read by id (reads).
    - SnapshotHost, seed, left_about, TASK_WORDS: the other stores, seeded and read (stores).
"""

from e2e.night_veil_hive.reads import (
    PURGED,
    counted,
    durable,
    kinds_about,
    leaks,
    night_veil_cells,
    succeeded,
    tasks,
    working,
    world,
)
from e2e.night_veil_hive.rig import (
    EVERYTHING,
    NightVeilRun,
    ProbeFactory,
    Snapshots,
    TeardownHook,
    abscond,
    green_probe,
    night_veil_hive,
    red_then_green,
    request,
    running,
    watch_teardown,
)
from e2e.night_veil_hive.stores import TASK_WORDS, SnapshotHost, left_about, seed

__all__ = [
    "EVERYTHING",
    "PURGED",
    "TASK_WORDS",
    "NightVeilRun",
    "ProbeFactory",
    "SnapshotHost",
    "Snapshots",
    "TeardownHook",
    "abscond",
    "counted",
    "durable",
    "green_probe",
    "kinds_about",
    "leaks",
    "left_about",
    "night_veil_cells",
    "night_veil_hive",
    "red_then_green",
    "request",
    "running",
    "seed",
    "succeeded",
    "tasks",
    "watch_teardown",
    "working",
    "world",
]
