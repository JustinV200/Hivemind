"""Provide `hive swarm packet`: build a self-contained Pollen Packet for one new device.

`hive swarm --manifest hive.toml packet --label NAME --platform windows-x86_64 [--access LEVEL]
[--shield meadow|propolis] [--out DIR] [--pollen FILE]` writes one folder that takes a new
machine from nothing to an enrolled Real Cell (a device the Hive borrows) on the Hive's tailnet
(its private Tailscale network): the cached pollen build for the platform, an installer script, a
README, a single-use tailnet key minted through Tailscale's API with the OAuth client
`[swarm.packet] tailscale_oauth` names, and a single-use invite minted exactly as `hive swarm
invite` mints one. Everything that can be refused without minting (no build, no OAuth source, an
advertised URL a device could not reach from another network, an existing folder) is refused
first; `hivemind.swarm.packet.build_packet` then mints the key before the invite and revokes both
if anything after fails. Standard output carries the packet's folder and nothing else; standard
error says what it is for and when it lapses. Neither ever carries a secret.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside the CLI's `swarm` command group. Mounted by
    `hivemind.cli.swarm` as `packet`. Calls into this group's `context`,
    `hivemind.cli.compose.swarm` (the invite door), `hivemind.cli.tools.context`,
    `hivemind.swarm` and `hivemind.swarm.packet`.

Key invariants:
    - The invite string and the tailnet key never reach standard output, standard error, a log
      line or the trail; only the folder, the key's id and the expiry are shown.
    - The environment is read here, once, and handed to the OAuth source (codingrules 13).

See Also:
    - docs/runbooks/enrolling-a-device.md, "Pollen Packet over Tailscale", for the whole path.
    - docs/adr/0067-the-pollen-packet-carries-a-tailnet-key-and-an-invite.md for the decision.
    - hivemind.cli.swarm.invite for the invite this shares its door with.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Annotated

import httpx
import typer

from hivemind.cell import AccessLevel
from hivemind.cli.compose.deps.parts import manifest_dir
from hivemind.cli.compose.swarm import build_invite_door
from hivemind.cli.landing import CarriedOption, carried_command, carried_path
from hivemind.cli.swarm.context import (
    DEFAULT_SHIELD,
    SwarmCliContext,
    cli_context,
    open_registry,
    operator_recorder,
    parse_access,
    parse_shield,
    require_swarm,
)
from hivemind.cli.swarm.invite import AccessOption, ShieldOption
from hivemind.cli.tools.context import EXIT_BAD_INPUT, run_or_exit
from hivemind.swarm import EnrolmentDoor, ListenerConfig, SwarmRecorder, SwarmRegistry
from hivemind.swarm.packet import (
    TAILSCALE_API,
    BuiltPacket,
    HiveTarget,
    PacketDeps,
    PacketOrder,
    PacketPlatform,
    TailscaleApiKeys,
    build_packet,
    find_pollen_build,
    parse_secret_source,
)

# Anything but letters, digits, `_`, `.` and `-` in a label becomes `-` in the default folder name.
_UNSAFE_NAME = re.compile(r"[^A-Za-z0-9_.-]+")

LabelOption = Annotated[
    str, typer.Option("--label", help="The device's name, shown by `hive swarm list` (64 chars).")
]
PlatformOption = Annotated[
    str,
    typer.Option(
        "--platform",
        help="The device's platform: " + ", ".join(p.value for p in PacketPlatform) + ".",
    ),
]
# `--out` and `--pollen` ride on the context: the command function is at codingrules 5.1's five
# parameters with the four options `hive swarm invite` also has (`PacketCommand` declares them).
OUT = CarriedOption(
    key="hivemind.cli.swarm.packet.out",
    decls=("--out",),
    help="The packet folder to create (default: ./pollen-packet-<label>).",
)
POLLEN = CarriedOption(
    key="hivemind.cli.swarm.packet.pollen",
    decls=("--pollen",),
    help="A pollen build to use instead of [swarm.packet] pollen_dir/<platform>/.",
)
PacketCommand = carried_command(OUT, POLLEN)

__all__ = ["PacketCommand", "packet_command"]


def packet_command(
    ctx: typer.Context,
    label: LabelOption,
    platform: PlatformOption,
    access: AccessOption = None,
    shield: ShieldOption = DEFAULT_SHIELD,
) -> None:
    """Build a Pollen Packet: pollen, an installer, a tailnet key and an invite, in one folder."""
    cli_ctx = cli_context(ctx)
    require_swarm(cli_ctx)
    out, pollen = carried_path(ctx, OUT), carried_path(ctx, POLLEN)
    request = _Request(label.strip(), platform, access, shield, out, pollen)
    registry, recorder = open_registry(cli_ctx), operator_recorder(cli_ctx)
    # Built outside the event loop: reading the Hive's signing key runs its own asyncio.run.
    door = build_invite_door(cli_ctx.manifest, registry, recorder)
    order, built = run_or_exit(_build(cli_ctx, request, (door, registry, recorder)))
    _print_packet(built, order)


@dataclass(frozen=True, slots=True)
class _Request:
    """The command's options, as typed."""

    label: str  # `--label`, stripped.
    platform: str  # `--platform`.
    access: str | None  # `--access`, or None for `[swarm] default_access`.
    shield: str  # `--shield`.
    out: Path | None  # `--out`, or None for ./pollen-packet-<label>.
    pollen: Path | None  # `--pollen`, or None for the cached build.


async def _build(
    cli_ctx: SwarmCliContext,
    request: _Request,
    parts: tuple[EnrolmentDoor, SwarmRegistry, SwarmRecorder],
) -> tuple[PacketOrder, BuiltPacket]:
    """Check the order, compose the Tailscale minter over the OAuth source, build the packet."""
    manifest, (door, registry, recorder) = cli_ctx.manifest, parts
    # Every refusal that needs no mint comes first, inside run_or_exit so it prints as one line.
    order = _order(cli_ctx, request)
    # The one read of the environment, handed to an `env:` source (codingrules section 13).
    source = parse_secret_source(manifest.swarm.packet.tailscale_oauth, os.environ)
    ttl = timedelta(minutes=manifest.swarm.invite_ttl_minutes)
    async with httpx.AsyncClient(base_url=TAILSCALE_API) as client:
        keys = TailscaleApiKeys(source, client)
        deps = PacketDeps(door, registry, keys, recorder, ttl, manifest.hive.id)
        return order, await build_packet(deps, order)


def _order(cli_ctx: SwarmCliContext, request: _Request) -> PacketOrder:
    """Turn the options into an order: the build, the target and the device's terms."""
    manifest = cli_ctx.manifest
    platform = _parse_platform(request.platform)
    url = ListenerConfig.from_section(manifest.swarm, manifest_dir(manifest)).invite_url
    pollen_dir = manifest.resolve_path(Path(manifest.swarm.packet.pollen_dir))
    default_folder = Path.cwd() / f"pollen-packet-{_folder_name(request.label)}"
    return PacketOrder(
        folder=request.out if request.out is not None else default_folder,
        platform=platform,
        pollen=request.pollen or find_pollen_build(pollen_dir, platform),
        target=HiveTarget.from_url(url),
        access=parse_access(request.access) if request.access else _default_access(cli_ctx),
        shield=parse_shield(request.shield),
        label=request.label,
    )


def _parse_platform(text: str) -> PacketPlatform:
    """Read `--platform`, or exit with the list of platforms there are."""
    try:
        return PacketPlatform(text.strip().lower())
    except ValueError:
        names = ", ".join(p.value for p in PacketPlatform)
        typer.echo(f"Unknown platform {text!r}; use one of: {names}.", err=True)
        raise typer.Exit(code=EXIT_BAD_INPUT) from None


def _default_access(cli_ctx: SwarmCliContext) -> AccessLevel:
    """The access level a packet's invite carries when `--access` is not given."""
    return AccessLevel.from_wire(cli_ctx.manifest.swarm.default_access)


def _folder_name(label: str) -> str:
    """A file-system-safe form of the label for the default folder name."""
    return _UNSAFE_NAME.sub("-", label).strip("-.") or "device"


def _print_packet(built: BuiltPacket, order: PacketOrder) -> None:
    """Say what was built on standard error, then print the folder alone on standard output."""
    # Ids and times only: the folder's secrets/ is the one place the secrets are.
    typer.echo(
        f"Pollen Packet for {order.label!r} ({order.platform.value}): tailnet key "
        f"{built.key_id}, usable once until {built.expires_at.isoformat()}.\n"
        f"Carry the folder to the device yourself and run {order.platform.installer_name} in "
        "it; README.txt says how. Its secrets/ folder is a key to the Hive until then.",
        err=True,
    )
    typer.echo(str(built.folder))
