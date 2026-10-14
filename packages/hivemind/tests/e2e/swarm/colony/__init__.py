"""Re-export the rig for a device that is moved up the ladder: colonized, then made a Nuc.

The phase 11 Nuc scenario puts a whole Hive in front of a container whose packet has the Hive
runtime to install and a manifest to give its Warden, and moves it from Level 0 to Level 1 to
Level 2 while a task runs. This package is what that needs beyond `e2e.swarm.bridge`: `models` is
the text the Hive's manifest and the device's agree on (the model server, the models it may load,
the rounds the scripted models make), `device` makes the container colonizable, serves the
scripted model on the Hive Stand's side of its cable and reads what the Warden on the device has
queued and recorded, `orders` listens at the Queen's door and sends the Warden an order of the
scenario's own, `trail` reads what the scenario asserts off the Hive's trail (what ran when, how
many at once, what arrived twice), `waits` is the bounded, named waiting, and `run` tells the whole
story in order and returns the record the exit test asserts over. `window` hears what the Queen's
replay window drops (a duplicate inside the reconnection grace leaves nothing on the trail) and
`unnoticed` tells the second story, a cable pulled under a task whose result is sent before the
loss is noticed, for `tests/e2e/test_phase11_exit_unnoticed.py`, and `desktop` tells the third, a
task that needs a display put to a colonized device whose image carries an X server, for
`tests/e2e/test_phase11_exit_exoskeleton.py`. `orders`, `trail`, `waits`, `run`, `window`,
`unnoticed` and `desktop` are imported from their modules, not from this face.

Fits into the Hive:
    Test infrastructure (codingrules section 14.3), not shipped. Used by
    `tests/e2e/test_phase11_exit_nuc.py` and the other colonized-device scenarios, and by their
    unit tests under `tests/unit/swarm`.

Key invariants:
    - This file holds re-exports and `__all__` only.

See Also:
    - e2e/swarm/bridge.py for the whole Hive this rig puts the device in front of.
    - tests/e2e/README.md for how the scenarios use it.

Public API:
    - ALLOWED, EXTRA, ROGUE, SMALL, SERVER_ID, device_manifest, hive_tables, local_scenario,
      shared_scenario: what the Hive and the device say about the model server (models).
    - colonizable, shared_model, scenario_of, device_logs: the container and the model on the Hive
      Stand's side of its cable (device).
"""

from e2e.swarm.colony.device import colonizable, device_logs, scenario_of, shared_model
from e2e.swarm.colony.models import (
    ALLOWED,
    EXTRA,
    ROGUE,
    SERVER_ID,
    SMALL,
    device_manifest,
    hive_tables,
    local_scenario,
    shared_scenario,
)

__all__ = [
    "ALLOWED",
    "EXTRA",
    "ROGUE",
    "SERVER_ID",
    "SMALL",
    "colonizable",
    "device_logs",
    "device_manifest",
    "hive_tables",
    "local_scenario",
    "scenario_of",
    "shared_model",
    "shared_scenario",
]
