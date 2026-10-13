"""Run the Pollen Packet's command line with ``python -m pollen``.

The service units that ``pollen install`` writes start the packet as ``<python> -m pollen run``, so
the unit never depends on what is on the service manager's PATH, and a frozen single-file build
runs the same entry point. This module only hands control to ``pollen.cli.main`` and exits with its
status.

Fits into the Hive:
    Parallel to hivemind's layer table, inside the pollen package. The module Python runs for
    ``python -m pollen``; calls into pollen.cli only.

Key invariants:
    - It holds no logic of its own: the command line is ``pollen.cli.main`` and nothing else.

See Also:
    - pollen.cli for the commands.
"""

from __future__ import annotations

import sys

from pollen.cli import main

if __name__ == "__main__":
    sys.exit(main())
