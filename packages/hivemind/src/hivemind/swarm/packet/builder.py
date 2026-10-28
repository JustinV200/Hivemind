"""Build one Pollen Packet: mint its tailnet key, then its invite, then write it, or undo all three.

`hive swarm packet` hands an operator one folder that takes a new device from nothing to an
enrolled Real Cell (a device the Hive borrows) on the Hive's private network. That folder carries
two live secrets, a single-use tailnet key and a single-use invite, so building it is ordered for
the failure case. The tailnet key is minted first, because it is the step most likely to fail (an
unreachable API, a lapsed OAuth client) and a refusal there has minted nothing. The invite comes
second, through the same enrolment door `hive swarm invite` uses, so the two commands cannot drift.
If the invite or the folder then fails, everything minted so far is revoked and the half-written
folder removed before the error propagates: no packet means no live secret. A built packet is
recorded on the Pheromone Trail (the Hive's audit log) as `swarm.packet_built`, with ids and counts
only; the invite's own `swarm.invited` is recorded by the door as always.

Fits into the Hive:
    Layer 3 (sources of Cells, and capabilities handed down), inside `hivemind.swarm.packet`.
    Called by `hive swarm packet` (Layer 7). Calls into the enrolment door and invite registry
    (`hivemind.swarm`), `TailnetKeys`, `writer` and `SwarmRecorder`.

Key invariants:
    - The key is minted before the invite; a failed key mint leaves no invite in the registry.
    - Any failure after a mint removes the folder, then revokes the invite and the key (best
      effort, each step attempted whatever happened to the one before). A key whose mint was
      still in flight when the build was cancelled is never seen here; it was never written
      anywhere and lapses at its TTL.
    - No secret is returned, logged or recorded: `BuiltPacket` holds the folder and the ids only.

See Also:
    - docs/adr/0067-the-pollen-packet-carries-a-tailnet-key-and-an-invite.md for the decision.
    - hivemind.swarm.enrolment.door for `mint_invite`.
"""

from __future__ import annotations

import asyncio
import contextlib
import shutil
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from hivemind.cell import AccessLevel, CombShieldLevel
from hivemind.common.errors import HiveMindError
from hivemind.pheromone import SwarmEvent
from hivemind.swarm.enrolment import EnrolmentDoor, MintedInvite
from hivemind.swarm.errors import PacketError
from hivemind.swarm.events import SwarmRecorder
from hivemind.swarm.packet.platforms import PacketPlatform
from hivemind.swarm.packet.tailnet import TailnetKey, TailnetKeys
from hivemind.swarm.packet.writer import HiveTarget, PacketFiles, write_packet
from hivemind.swarm.registry import InviteRegistry

__all__ = ["BuiltPacket", "PacketDeps", "PacketOrder", "build_packet"]


@dataclass(frozen=True, slots=True)
class PacketDeps:
    """The Hive Stand's side of a packet: who mints, who records, and how long it lives."""

    door: EnrolmentDoor  # Mints the invite exactly as `hive swarm invite` does.
    invites: InviteRegistry  # Revokes the invite if the packet is not built.
    keys: TailnetKeys  # Mints and revokes the tailnet key.
    recorder: SwarmRecorder  # Records `swarm.packet_built`, as the operator.
    ttl: timedelta  # `[swarm] invite_ttl_minutes`: the key lapses with the invite.
    hive_id: str  # The trail subject a packet is recorded against: the Hive itself.


@dataclass(frozen=True, slots=True)
class PacketOrder:
    """What the operator asked for: the device's terms, its platform and where the packet goes."""

    folder: Path  # The packet folder; must not exist yet.
    platform: PacketPlatform  # Which build and which installer.
    pollen: Path  # The cached build for that platform.
    target: HiveTarget  # The swarm listener's host and port, from its advertised URL.
    access: AccessLevel  # The most the device may be granted.
    shield: CombShieldLevel  # The Comb Shield tier the operator vouches for.
    label: str  # The device's name in `hive swarm list`.


@dataclass(frozen=True, slots=True)
class BuiltPacket:
    """A packet that was written: where, and the ids of what it carries. No secret."""

    folder: Path  # The packet folder.
    key_id: str  # Tailscale's id for the tailnet key.
    code_hash: str  # The invite's registry key (the SHA-256 of its token).
    expires_at: datetime  # When the invite lapses; the key lapses no later.


async def build_packet(deps: PacketDeps, order: PacketOrder) -> BuiltPacket:
    """Mint a tailnet key and an invite, and write the packet; leave nothing live on failure.

    Args:
        deps: The minters, the recorder and the lifetime.
        order: The device's terms and where the packet goes.

    Returns:
        Where the packet is and the ids of its key and invite.

    Raises:
        PacketError: The folder exists, the key could not be minted, or the secrets folder could
            not be made owner-only. Anything minted is revoked, and the folder removed, first.
        InviteRefusedError: The shield is Night Veil; the key is revoked first.
        OSError: A file could not be written; what was minted is revoked first.
    """
    _claim(order.folder)
    key: TailnetKey | None = None
    minted: MintedInvite | None = None
    is_built = False
    try:
        # The key first: the likeliest failure, and a refusal here has minted nothing.
        key = await deps.keys.mint(deps.ttl)
        minted = await deps.door.mint_invite(order.access, order.shield, order.label)
        count = await asyncio.to_thread(_write, order, minted, key)
        await deps.recorder.record(_built_event(deps, minted, key, order, count))
        is_built = True
        return BuiltPacket(order.folder, key.key_id, minted.code_hash, minted.expires_at)
    finally:
        # Any failure, cancellation included: nothing minted may stay live without its packet.
        if not is_built:
            await _undo(deps, order.folder, key, minted)


def _claim(folder: Path) -> None:
    """Create the packet folder, refusing one that exists: it may hold an older packet's secrets."""
    try:
        folder.mkdir(parents=True)
    except FileExistsError:
        raise PacketError(f"{folder} already exists; name a new folder with --out.") from None


def _write(order: PacketOrder, minted: MintedInvite, key: TailnetKey) -> int:
    """Write the packet folder (blocking file work, run on a worker thread)."""
    files = PacketFiles(order.platform, order.pollen, order.target, order.label, minted.expires_at)
    return write_packet(order.folder, files, minted.invite_string, key.key)


def _built_event(
    deps: PacketDeps, minted: MintedInvite, key: TailnetKey, order: PacketOrder, count: int
) -> SwarmEvent:
    """Build `swarm.packet_built`: the key's and invite's ids, the platform and a file count."""
    return deps.recorder.swarm_event(
        "swarm.packet_built",
        deps.hive_id,
        {
            "tailnet_key_id": key.key_id,
            "invite_code_hash": minted.code_hash,
            "platform": order.platform.value,
            "files": count,
        },
    )


async def _undo(
    deps: PacketDeps, folder: Path, key: TailnetKey | None, minted: MintedInvite | None
) -> None:
    """Remove the folder, then revoke the invite, then the key, keeping the original error."""
    # The secrets on disk go first: they are the copy someone could walk off with. Each later step
    # runs in a `finally`, so an error or a cancellation in one never skips the next; each is best
    # effort, because the error that brought us here is the one the operator must see, and a key or
    # invite that could not be revoked still lapses within the invite's lifetime.
    try:
        # The folder is this build's own (`_claim` made it); a small tree, on a worker thread.
        await asyncio.to_thread(shutil.rmtree, folder, ignore_errors=True)
    finally:
        try:
            if minted is not None:
                with contextlib.suppress(HiveMindError, OSError):
                    await deps.invites.revoke_invite(minted.code_hash)
        finally:
            if key is not None:
                with contextlib.suppress(HiveMindError, OSError):
                    await deps.keys.revoke(key.key_id)
