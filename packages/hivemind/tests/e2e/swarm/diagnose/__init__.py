"""Re-export the rig for the diagnosis scenarios: a Hive, a container and the faults done to it.

The last exit criterion of phase 11 has three cases, and each is a whole Hive in front of a
container that is cut off from it in a way of its own. This package is what they share and what
each adds. `scene` builds the Hive and enrols the device, climbs it to the rung the case needs
and puts a task on it (`run_case`, `Scene`, `Setup`); `scripts` has the rounds each scripted Drone
makes, on the device and on the Hive Stand; `plant` does things to the container (a wrong line in
`/etc/hosts`) and to its links, and reads the name the way the Drone does; `reads` joins the
Nectar the Hive stored with the trail event that recorded it; and `reversible`, `unfixable` and
`unplugged` tell the three cases and return the record each test asserts over. The case modules are
imported from their own modules, not from this face.

Fits into the Hive:
    Test infrastructure (codingrules section 14.3), not shipped. Used by
    `tests/e2e/test_phase11_exit_diagnose*.py` and their unit tests under `tests/unit/swarm`.

Key invariants:
    - This file holds re-exports and `__all__` only.

See Also:
    - e2e/swarm/colony/ for the rig this is cut from (the Nuc whose cable is pulled).
    - tests/e2e/README.md for how the scenarios use it.

Public API:
    - Scene, Setup, run_case, LADDER: the Hive, the device and the way to tell a story (scene).
    - DIAGNOSIS_MARK, TASK_MARK, mend_hosts, lost_network, stand_check: the scripted rounds
      (scripts).
    - plant_wrong_entry, sever, address_of, hosts_text, wait_for_address, WRONG_ADDRESS: what is
      done to the container and its links, and how the name is read (plant).
    - Deposit, deposits, findings_of, outcome_of, content_of, for_device: the Nectar the Hive
      stored (reads).
"""

from e2e.swarm.diagnose.plant import (
    WRONG_ADDRESS,
    address_of,
    hosts_text,
    plant_wrong_entry,
    sever,
    wait_for_address,
)
from e2e.swarm.diagnose.reads import (
    Deposit,
    content_of,
    deposits,
    findings_of,
    for_device,
    outcome_of,
)
from e2e.swarm.diagnose.scene import LADDER, Scene, Setup, run_case
from e2e.swarm.diagnose.scripts import (
    DIAGNOSIS_MARK,
    TASK_MARK,
    lost_network,
    mend_hosts,
    stand_check,
)

__all__ = [
    "DIAGNOSIS_MARK",
    "LADDER",
    "TASK_MARK",
    "WRONG_ADDRESS",
    "Deposit",
    "Scene",
    "Setup",
    "address_of",
    "content_of",
    "deposits",
    "findings_of",
    "for_device",
    "hosts_text",
    "lost_network",
    "mend_hosts",
    "outcome_of",
    "plant_wrong_entry",
    "run_case",
    "sever",
    "stand_check",
    "wait_for_address",
]
