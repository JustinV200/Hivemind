"""Provide the Hive's command-line interface: one thin file per command group.

Each command group is a thin typer layer that calls into a subsystem's public API and never
contains logic of its own.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard). Called by an operator at a terminal. Calls into
    queen (Layer 6) and below, through each subsystem's public API.

Key invariants:
    - `main` is the only thing outside this package that should ever be called; it is the
      console-script entry point and the sole way into the CLI.

See Also:
    - .claude/codingrules.md section 4 for the layer 7 row this package occupies.
    - .claude/roadmap.md phase 0 for the work that first populates this package.

Public API:
    - main: console-script entry point (`hive`); builds and runs the typer application.
"""

from hivemind.cli.app import main

__all__ = ["main"]
