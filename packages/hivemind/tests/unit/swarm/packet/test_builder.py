"""Tests for hivemind.swarm.packet.builder: a packet is written whole, or nothing stays live.

Fits into the Hive:
    Mirrors src/hivemind/swarm/packet/builder.py (codingrules section 3). Drives `build_packet`
    over the in-memory Hive Stand of `builders.swarm.make_hive_rig` (a real enrolment door, a
    memory registry and trail) and `FakeTailnetKeys`, so no tailnet is ever dialled. The written
    folder is read back from disk: its layout, its filled installer, its owner-only secrets, and
    the trail event, which must carry ids and counts and never a secret.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.swarm.packet.builder for the module under test.
    - docs/adr/0067-the-pollen-packet-carries-a-tailnet-key-and-an-invite.md.
"""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path
from typing import NoReturn

import pytest
from builders.swarm import HiveRig, make_hive_rig

from hivemind.cell import AccessLevel, CombShieldLevel
from hivemind.common.errors import HiveMindError
from hivemind.swarm import InviteState, parse_invite_string
from hivemind.swarm.errors import PacketError
from hivemind.swarm.packet import (
    AUTH_KEY_FILE,
    INVITE_FILE,
    SECRETS_DIR,
    FakeTailnetKeys,
    HiveTarget,
    PacketDeps,
    PacketOrder,
    PacketPlatform,
    build_packet,
    is_owner_only,
)

_TTL = timedelta(minutes=15)  # The default invite lifetime the key must not outlive.
_TARGET = HiveTarget(host="hivestand.example-tailnet.ts.net", port=8720)


def _deps(rig: HiveRig, keys: FakeTailnetKeys) -> PacketDeps:
    """The Hive Stand's side of a packet, over the rig's door, registry and trail."""
    return PacketDeps(rig.door, rig.registry, keys, rig.recorder, _TTL, rig.hive.hive_id)


def _order(tmp_path: Path, platform: PacketPlatform, pollen: Path | None = None) -> PacketOrder:
    """An order for a SCRATCH, Meadow device called kitchen-pc, with a fake build on disk."""
    build = tmp_path / "build" / platform.binary_name
    build.parent.mkdir(exist_ok=True)
    build.write_bytes(b"pollen build")
    return PacketOrder(
        folder=tmp_path / "out" / "packet",
        platform=platform,
        pollen=build if pollen is None else pollen,
        target=_TARGET,
        access=AccessLevel.SCRATCH,
        shield=CombShieldLevel.MEADOW,
        label="kitchen-pc",
    )


@pytest.mark.parametrize("platform", [PacketPlatform.WINDOWS_X86_64, PacketPlatform.LINUX_ARM64])
async def test_build_packet_writes_the_build_installer_and_owner_only_secrets(
    tmp_path: Path, platform: PacketPlatform
) -> None:
    rig, keys = make_hive_rig(), FakeTailnetKeys()

    built = await build_packet(_deps(rig, keys), _order(tmp_path, platform))

    folder, secrets = built.folder, built.folder / SECRETS_DIR
    assert (folder / platform.binary_name).read_bytes() == b"pollen build"
    installer = (folder / platform.installer_name).read_text(encoding="utf-8")
    assert _TARGET.host in installer and "8720" in installer and "@@" not in installer
    assert "@@" not in (folder / "README.txt").read_text(encoding="utf-8")
    # The two secrets are the live ones, and only their owner can open them.
    invite = (secrets / INVITE_FILE).read_text(encoding="utf-8")
    key = (secrets / AUTH_KEY_FILE).read_text(encoding="utf-8")
    assert key == keys.live[built.key_id].key.get_secret_value() and keys.ttls == [_TTL]
    assert parse_invite_string(invite).label == "kitchen-pc"
    assert all(
        is_owner_only(path) for path in (secrets, secrets / INVITE_FILE, secrets / AUTH_KEY_FILE)
    )
    # The trail has ids and counts, and neither secret.
    (event,) = await rig.events("swarm.packet_built")
    assert event.payload["tailnet_key_id"] == built.key_id and event.payload["files"] == 5
    recorded = json.dumps([e.payload for e in await rig.events()])
    assert key not in recorded and invite.strip() not in recorded


@pytest.mark.parametrize(
    "failure", ["key_refused", "write_failed", "invite_revoke_fails", "folder_exists"]
)
async def test_build_packet_leaves_no_live_key_or_invite_when_a_step_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    # Arrange one failure: Tailscale refuses the key, the build vanished before the copy (and, in
    # one row, revoking the invite then fails too), or the folder is already there (and may hold
    # an older packet's secrets).
    rig, keys = make_hive_rig(), FakeTailnetKeys(refuse=failure == "key_refused")
    order = _order(tmp_path, PacketPlatform.WINDOWS_X86_64)
    if failure in {"write_failed", "invite_revoke_fails"}:
        order.pollen.unlink()
    if failure == "invite_revoke_fails":
        monkeypatch.setattr(rig.registry, "revoke_invite", _refuse_revoke)
    if failure == "folder_exists":
        order.folder.mkdir(parents=True)
        (order.folder / "older.txt").write_text("keep me", encoding="utf-8")

    with pytest.raises(PacketError):
        await build_packet(_deps(rig, keys), order)

    # A failed invite revocation must not stop the key's revocation after it.
    assert keys.live == {}
    invites = await rig.registry.invites()
    if failure != "invite_revoke_fails":
        assert not [i for i in invites if i.state is InviteState.ISSUED]
    # The key is minted first, so when it is refused no invite was ever written.
    assert failure != "key_refused" or invites == ()
    # A folder this build made is gone; one that was there before is left untouched.
    assert order.folder.exists() == (failure == "folder_exists")
    if failure == "folder_exists":
        assert (order.folder / "older.txt").read_text(encoding="utf-8") == "keep me"


async def _refuse_revoke(code_hash: str, event: object = None) -> NoReturn:
    """Stand in for a registry whose invite revocation fails."""
    raise HiveMindError(f"cannot revoke {code_hash[:8]}")
