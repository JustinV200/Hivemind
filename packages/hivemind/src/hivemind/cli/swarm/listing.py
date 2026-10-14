"""Provide `hive swarm list`: every enrolled device, its rung, reachability and terms.

`hive swarm --manifest hive.toml list [--json]` reads the swarm registry (the Hive's table of
enrolled devices) straight from the Hive's database and prints one row per device: device id,
label, Cell, runtime level (and the level the operator asked for, if the Queen has not got there
yet), status, access level, Comb Shield tier and when it last spoke. Revoked devices stay in the
table, marked `REVOKED`, because the registry keeps them for the audit. It needs no running Hive:
it shows what the registry holds, and the status column is whatever the last Hive to run left
there (a Hive that is down cannot have noticed a device go).

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside the CLI's `swarm` command group. Mounted by
    `hivemind.cli.swarm` as `list`. Calls into this group's own `context` and `render`, and
    `hivemind.cli.stores` (the `--json` option) and `hivemind.cli.tools.context` only.

Key invariants:
    - Read-only: nothing is written or recorded.
    - The table and `--json` print the same fields (`render.NodeRow`).

See Also:
    - docs/waggle/enrolment.md section 8 for what moves a device between ONLINE and UNREACHABLE.
    - hivemind.cli.swarm.cells for the same devices as rows of `hive cells list`.
"""

from __future__ import annotations

import typer

from hivemind.cli.stores import JsonOption
from hivemind.cli.swarm.context import cli_context, open_registry
from hivemind.cli.swarm.render import print_nodes
from hivemind.cli.tools.context import run_or_exit

__all__ = ["list_command"]


def list_command(ctx: typer.Context, as_json: JsonOption = False) -> None:
    """List every enrolled device: id, label, Cell, level, status, access, tier and last seen."""
    registry = open_registry(cli_context(ctx))
    nodes = run_or_exit(registry.nodes())
    print_nodes(nodes, as_json=as_json)
