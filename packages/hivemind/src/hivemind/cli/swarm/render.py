"""Render `hive swarm`'s output: the device table, its JSON form, and the sentence naming a device.

`hive swarm list` shows one row per enrolled device (an existing machine the Hive borrows as a
Real Cell): its device id, the label the operator gave it, its Cell id, the rung of the runtime
ladder it is on (and the rung the operator asked for, if the Queen has not got there yet), whether
the Hive can reach it, its access level and Comb Shield tier, and when it last spoke. `NodeRow` is
that row as a model, so the table and `--json` print the same fields, and nothing a device could
use to impersonate itself (its public key, its granted capability strings) is ever among them.
`describe` is the short way every other command names a device in its one-line answers.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside the CLI's `swarm` command group. Imported
    by `listing` and `changes`. Calls into `hivemind.swarm` and pydantic only.

Key invariants:
    - A row holds identifiers, names and a time: no key, token, hostname or capability string.
    - The table and the JSON are built from the same `NodeRow`, so they cannot disagree.
    - Nothing here reads a store; the caller hands in the nodes.

See Also:
    - hivemind.cli.tools.render for the same split of a table from its model.
    - hivemind.swarm.models for the `SwarmNode` each row is made from.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import datetime

import typer
from pydantic import BaseModel, ConfigDict, Field

from hivemind.swarm import SwarmNode

_NEVER = "-"  # What a column shows when there is nothing to show: no label, no wish, never seen.
_LABEL_WIDTH = 16  # A label is the operator's own short name; longer ones are cut in the table.
_TIME_FORMAT = "%Y-%m-%dT%H:%M:%SZ"  # Seconds, in UTC: a heartbeat is never finer than that.

__all__ = ["NodeRow", "describe", "node_row", "print_nodes"]


class NodeRow(BaseModel):
    """One `hive swarm list` row: an enrolled device's identity, rung, reachability and terms."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    device_id: str = Field(description="The device's bee address, for life.")
    label: str | None = Field(description="The operator's name from the invite, if any.")
    cell_id: str = Field(description="The Real Cell the device is.")
    level: str = Field(description="The rung it is on: GATEWAY, COLONIZED or NUC.")
    desired_level: str | None = Field(
        description="The rung the operator asked for with promote or demote and the Queen has "
        "not reached yet; null when nothing is pending."
    )
    status: str = Field(description="ONLINE, UNREACHABLE or REVOKED.")
    access_level: str = Field(description="How much of the device the Hive may touch.")
    comb_shield: str = Field(description="Its security tier: MEADOW or PROPOLIS.")
    last_seen_at: datetime | None = Field(
        description="When a verified frame last arrived; null before the first."
    )


def node_row(node: SwarmNode) -> NodeRow:
    """Build the row for one enrolled device.

    Args:
        node: The device as the registry holds it.

    Returns:
        Its row.
    """
    wanted = node.desired_level
    return NodeRow(
        device_id=node.device_id,
        label=node.label,
        cell_id=node.cell_id,
        level=node.level.value,
        desired_level=wanted.value if wanted is not None else None,
        status=node.status.value,
        access_level=node.access_level.value,
        comb_shield=node.comb_shield.value,
        last_seen_at=node.last_seen_at,
    )


def describe(node: SwarmNode) -> str:
    """Name a device in a sentence: its device id, and its label when it has one.

    Args:
        node: The device.

    Returns:
        `device_<id> (label)`, or just the id.
    """
    return f"{node.device_id} ({node.label})" if node.label else str(node.device_id)


def print_nodes(nodes: Sequence[SwarmNode], *, as_json: bool) -> None:
    """Print the enrolled devices as a fixed-width table, or as one JSON array.

    Args:
        nodes: The devices, oldest enrolment first.
        as_json: Print JSON instead of a table.
    """
    rows = [node_row(node) for node in nodes]
    if as_json:
        typer.echo(json.dumps([row.model_dump(mode="json") for row in rows], indent=2))
        return
    if not rows:
        # The hint goes to standard error, so a script reading the table still gets only the header.
        typer.echo("No device is enrolled yet: mint an invite with `hive swarm invite`.", err=True)
    typer.echo(
        f"{'DEVICE':<33}  {'LABEL':<{_LABEL_WIDTH}}  {'CELL':<31}  {'LEVEL':<10}  {'WANTS':<10}  "
        f"{'STATUS':<11}  {'ACCESS':<9}  {'TIER':<9}  LAST SEEN"
    )
    for row in rows:
        seen = row.last_seen_at.strftime(_TIME_FORMAT) if row.last_seen_at else _NEVER
        typer.echo(
            f"{row.device_id:<33}  {(row.label or _NEVER):<{_LABEL_WIDTH}.{_LABEL_WIDTH}}  "
            f"{row.cell_id:<31}  {row.level:<10}  {(row.desired_level or _NEVER):<10}  "
            f"{row.status:<11}  {row.access_level:<9}  {row.comb_shield:<9}  {seen}"
        )
