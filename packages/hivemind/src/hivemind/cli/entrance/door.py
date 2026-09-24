"""Provide ``hive entrance reduce|open|status|expose``: the door's mode, and what exposes it.

The Hive Entrance (the Hive's one HTTP door) is OPEN or REDUCED (loopback only, every remote session
ended), and its remote listener exists only in the exposure mode ``[entrance] expose`` asks for and
this host can honour (ADR-0033). ``reduce`` narrows the door, which is always safe; ``open``
reopens it on loopback after a step-up the console gives with the password it holds. ``status``
reads the mode, the listeners, the devices by status and the requests held for a person, beside
the exposure plan the manifest yields on this host. ``expose`` prints that plan, or the rule the
host breaks, without starting anything and without a password: it is ``hive serve``'s own check
(``gather_facts`` then ``plan_exposure``), run dry.

Fits into the Hive:
    Layer 7 (the terminal), inside ``hivemind.cli.entrance``. Registered by
    ``hivemind.cli.entrance.group``. Calls into ``hivemind.cli.entrance.console`` and
    ``.serving``, the Landing Board's models and ``hivemind.entrance.expose``.

Key invariants:
    - ``expose`` never binds, listens or writes anything.
    - Reopening happens only through the loopback route, after a step-up.

See Also:
    - hivemind.entrance.reducer for the Entrance Reducer.
    - hivemind.entrance.expose.plan for the exposure rules.
"""

from __future__ import annotations

import asyncio
import json
from collections import Counter

import typer
from pydantic import BaseModel, ConfigDict, Field, JsonValue

from hivemind.cli.entrance.console import Stand, run_console
from hivemind.cli.entrance.serving import read_serve_record
from hivemind.cli.landing import SignedIn
from hivemind.cli.stores import DEFAULT_MANIFEST, JsonOption, ManifestOption, load_manifest_or_exit
from hivemind.entrance.expose import (
    ExposurePlan,
    ExposureRefusedError,
    ListenerPlan,
    SystemInterfaces,
    gather_facts,
    plan_exposure,
)
from hivemind.entrance.models import ChangedView, ConfirmationList, DeviceList, ModeView
from hivemind.manifest import HiveManifest
from waggle.clock import SystemClock

__all__ = [
    "EntranceStatus",
    "expose_command",
    "open_command",
    "plan_json",
    "plan_lines",
    "reduce_command",
    "status_command",
]


class EntranceStatus(BaseModel):
    """What ``hive entrance status`` reports, as its ``--json`` prints it."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    mode: str = Field(description="OPEN or REDUCED.")
    exposed: bool = Field(description="The manifest asks for a remote listener.")
    remote_listening: bool = Field(description="The remote listener serves right now.")
    loopback: str | None = Field(description="Where the loopback listener answers.")
    serve_pid: int | None = Field(description="The hive serve process.")
    devices: dict[str, int] = Field(description="How many devices are in each status.")
    held_confirmations: int = Field(description="Requests waiting for a person's confirmation.")
    plan: dict[str, JsonValue] | None = Field(description="The exposure plan on this host.")
    plan_refused: str | None = Field(description="Why this host refuses the manifest's mode.")


def reduce_command(ctx: typer.Context) -> None:
    """Reduce the Entrance to loopback only, ending every remote session."""

    async def narrow(board: SignedIn, _stand: Stand) -> ChangedView:
        """Reduce on loopback."""
        return await board.call("POST", "/v1/entrance/reduce", None, ChangedView)

    changed = run_console(ctx, "reduce", narrow)
    if changed.changed:
        typer.echo("Reduced: the Entrance serves loopback only; every remote session ended.")
        return
    typer.echo(f"Nothing changed: the Entrance was already {changed.mode.value}.")


def open_command(ctx: typer.Context) -> None:
    """Reopen a reduced Entrance (loopback only, after a step-up)."""

    async def reopen(board: SignedIn, _stand: Stand) -> ChangedView:
        """Reopen on loopback; the console steps up when asked."""
        return await board.call("POST", "/v1/entrance/open", None, ChangedView)

    changed = run_console(ctx, "open", reopen)
    if changed.changed:
        typer.echo("Reopened: the remote listener serves again where the manifest exposes it.")
        return
    typer.echo(f"Nothing changed: the Entrance was already {changed.mode.value}.")


def status_command(ctx: typer.Context, as_json: JsonOption = False) -> None:
    """Show the Entrance's mode, listeners, exposure plan, devices and held requests."""

    async def read(board: SignedIn, stand: Stand) -> EntranceStatus:
        """Read the running door and plan the manifest's exposure on this host."""
        mode = await board.call("GET", "/v1/entrance/mode", None, ModeView)
        devices = await board.call("GET", "/v1/devices", None, DeviceList)
        held = await board.call("GET", "/v1/entrance/confirmations", None, ConfirmationList)
        return await _status(stand, mode, devices, held)

    status = run_console(ctx, "status", read)
    if as_json:
        typer.echo(status.model_dump_json(indent=2))
        return
    for line in _status_lines(status):
        typer.echo(line)


def expose_command(
    manifest: ManifestOption = DEFAULT_MANIFEST, as_json: JsonOption = False
) -> None:
    """Print the exposure plan the manifest yields on this host, or the rule it breaks."""
    loaded = load_manifest_or_exit(manifest)
    try:
        plan = asyncio.run(_plan(loaded))
    except ExposureRefusedError as exc:
        typer.echo(f"hive entrance expose: {exc}", err=True)
        raise typer.Exit(code=2) from exc
    if as_json:
        typer.echo(json.dumps(plan_json(plan), indent=2))
        return
    for line in plan_lines(plan):
        typer.echo(line)
    typer.echo("Nothing was started.")


def plan_json(plan: ExposurePlan) -> dict[str, JsonValue]:
    """An exposure plan as JSON: the mode, both listeners, the origin, the relying party.

    Args:
        plan: What ``plan_exposure`` decided.

    Returns:
        A JSON object; paths are shown, never file contents.
    """
    return {
        "mode": plan.mode.value,
        "loopback": _listener_json(plan.loopback),
        "remote": _listener_json(plan.remote) if plan.remote is not None else None,
        "public_origin": plan.public_origin,
        "rp_id": plan.rp_id,
        "tunnel_argv": list(plan.tunnel_argv),
    }


def plan_lines(plan: ExposurePlan) -> list[str]:
    """An exposure plan as the operator reads it.

    Args:
        plan: What ``plan_exposure`` decided.

    Returns:
        One line per listener and name.
    """
    lines = [
        f"Exposure: {plan.mode.value}",
        f"  loopback listener: {_address(plan.loopback)} (always on)",
    ]
    remote = plan.remote
    if remote is None:
        lines.append("  remote listener: none (loopback only)")
        return lines
    tls = remote.tls
    how = "no TLS" if tls is None else f"TLS as {tls.server_name}"
    if tls is not None and tls.client_certificate_required:
        how += ", client certificates required"
    lines.append(f"  remote listener: {_address(remote)} ({how})")
    lines.append(f"  public origin: {plan.public_origin}; relying party: {plan.rp_id}")
    if plan.tunnel_argv:
        lines.append(f"  tunnel client: {' '.join(plan.tunnel_argv)}")
    return lines


async def _status(
    stand: Stand, mode: ModeView, devices: DeviceList, held: ConfirmationList
) -> EntranceStatus:
    """Assemble the status from what the Entrance answered and this host's exposure plan."""
    record = read_serve_record(stand.db)
    plan: dict[str, JsonValue] | None = None
    refused: str | None = None
    try:
        plan = plan_json(await _plan(stand.manifest))
    except ExposureRefusedError as exc:
        # The running serve already passed this check; a refusal now means the host changed.
        refused = str(exc)
    return EntranceStatus(
        mode=mode.mode.value,
        exposed=mode.exposed,
        remote_listening=mode.remote_listening,
        loopback=record.origin if record is not None else None,
        serve_pid=record.pid if record is not None else None,
        devices=dict(Counter(device.status.value for device in devices.devices)),
        held_confirmations=len(held.confirmations),
        plan=plan,
        plan_refused=refused,
    )


def _status_lines(status: EntranceStatus) -> list[str]:
    """The status as the operator reads it."""
    remote = "serving" if status.remote_listening else "not serving"
    lines = [
        f"Entrance: {status.mode} (hive serve pid {status.serve_pid}, loopback {status.loopback})",
        f"  remote listener: {remote}; exposed by the manifest: {status.exposed}",
    ]
    counts = ", ".join(f"{count} {name}" for name, count in sorted(status.devices.items()))
    lines.append(f"  devices: {counts or 'none'}")
    lines.append(f"  requests held for a person: {status.held_confirmations}")
    if status.plan_refused is not None:
        lines.append(f"  exposure plan now refused: {status.plan_refused}")
    return lines


async def _plan(manifest: HiveManifest) -> ExposurePlan:
    """Check ``[entrance]`` against this host, exactly as ``hive serve`` does before binding."""
    facts = await gather_facts(
        manifest.entrance, SystemInterfaces(), SystemClock(), manifest.resolve_path
    )
    return plan_exposure(manifest.entrance, facts)


def _listener_json(listener: ListenerPlan) -> dict[str, JsonValue]:
    """One listener as JSON: where it binds and how it speaks TLS."""
    tls = listener.tls
    return {
        "host": listener.host,
        "port": listener.port,
        "tls": None
        if tls is None
        else {
            "cert_path": str(tls.cert_path),
            "server_name": tls.server_name,
            "client_certificate_required": tls.client_certificate_required,
        },
    }


def _address(listener: ListenerPlan) -> str:
    """A listener's host and port; port 0 is the system's choice."""
    port = "a port the system picks" if listener.port == 0 else str(listener.port)
    return f"{listener.host}:{port}"
