"""Re-export the rig that proves the single-file packet runs on a device with no Python.

The Pollen Packet is shipped as one PyInstaller file, built inside a Debian 12 container so that
its glibc floor is Raspberry Pi OS's. Whether such a file installs and works on a machine that
has nothing but Debian 12's base system is a question for a machine like that, so this package is
the x86-64 stand-in for one: `build` makes the file the way the packaging workflow does and keeps
it under a key made of its inputs, `clean` is the bare Debian container an operator copies it to
and the commands they run there, and `stand` is a whole Hive on the docker bridge, with a
scripted Drone, for the packet to dial and be given a task by.

Fits into the Hive:
    Test infrastructure (codingrules section 14.2), not shipped. Used by
    `tests/e2e/test_phase11_exit_binary.py`; needs a docker daemon (`e2e.swarm.daemon_answers` is
    the skip probe).

Key invariants:
    - This file holds re-exports and `__all__` only.

See Also:
    - .github/workflows/pollen.yml for the workflow whose Linux legs this repeats.
    - .claude/phase-11-handoff.md section 5 for the exit criterion it serves.

Public API:
    - build_binary, binary_digest, BUILD_IMAGE, BUILD_TIMEOUT_S: the single-file packet and the
      key it is cached under (build).
    - CleanDevice, start_clean, CLEAN_IMAGE, INSTALL_PATH, OPERATOR, ROOT, POLLEN_DIR, UNIT_PATH: a
      bare Debian 12 device, the account its operator works as and what they do with a binary on
      it (clean).
    - DeviceDrone, bridge_hive, bridge_manifest, idle_warden, run_goal_on: a whole Hive a
      container can dial, its manifest, the scripted Drone and the placement of a goal on the
      device (stand).
"""

from e2e.swarm.binary.build import BUILD_IMAGE, BUILD_TIMEOUT_S, binary_digest, build_binary
from e2e.swarm.binary.clean import (
    CLEAN_IMAGE,
    INSTALL_PATH,
    OPERATOR,
    POLLEN_DIR,
    ROOT,
    UNIT_PATH,
    CleanDevice,
    start_clean,
)
from e2e.swarm.binary.stand import (
    DeviceDrone,
    bridge_hive,
    bridge_manifest,
    idle_warden,
    run_goal_on,
)

__all__ = [
    "BUILD_IMAGE",
    "BUILD_TIMEOUT_S",
    "CLEAN_IMAGE",
    "INSTALL_PATH",
    "OPERATOR",
    "POLLEN_DIR",
    "ROOT",
    "UNIT_PATH",
    "CleanDevice",
    "DeviceDrone",
    "binary_digest",
    "bridge_hive",
    "bridge_manifest",
    "build_binary",
    "idle_warden",
    "run_goal_on",
    "start_clean",
]
