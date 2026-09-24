"""Test hivemind.cli.remote.enrol: redeem an invite with a fresh key and keep the profile.

The redemption runs against ``hive serve``'s own composition on a loopback port; the operator's
invite comes from ``hive entrance invite``, exactly as a person would read it out.

Fits into the Hive:
    Mirrors src/hivemind/cli/remote/enrol.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from builders.entrance.stand import json_of, serving_stand, set_password, stand_manifest

from hivemind.cli.landing import LandingError, LandingRefusedError
from hivemind.cli.remote import EnrolmentOrder, ProfileStore, enrol_device, read_invite_link
from waggle.clock import SystemClock

_CODE = "ABCD-EFGH-IJKL-MNOP-QRST-UVWX-YY"  # A well-formed code no invite was minted for.
_HIVE = "hive_01J8Z3Q4X5Y6Z7A8B9C0D1E2F3"


def test_an_invite_link_carries_the_code_and_may_carry_the_hive() -> None:
    bare = read_invite_link("https://hive.example.ts.net:8711")
    link = read_invite_link(f"http://localhost:8710/enrol#code={_CODE}&hive={_HIVE}")

    assert (bare.code, bare.hive_id) == (None, None)
    assert (link.code, link.hive_id) == (_CODE, _HIVE)


async def test_a_fresh_key_redeems_the_invite_and_the_profile_is_kept(tmp_path: Path) -> None:
    path = stand_manifest(tmp_path / "stand")
    await set_password(path)
    store = ProfileStore(tmp_path / "laptop")

    async with serving_stand(path) as (stand, _entrance):
        invite = json_of(await stand.entrance("invite", "--device", "laptop", "--json"))
        order = EnrolmentOrder(
            link=invite["url"],
            code=None,
            hive_id=invite["hive_id"],
            name="laptop",
            profile="garden",
        )
        profile = await enrol_device(store, order, SystemClock())
        pending = await stand.entrance("pending")
        with pytest.raises(LandingRefusedError):
            again = EnrolmentOrder(invite["url"], None, invite["hive_id"], "copy", "other")
            await enrol_device(store, again, SystemClock())

    assert profile.device_id == invite["device_id"]
    assert profile.entrance_url.startswith("http://localhost:")
    assert profile.fingerprint in pending.output
    assert store.names() == ("garden",)
    assert (await store.signer(profile)).public_key_bytes


async def test_a_signature_over_another_hive_id_is_refused(tmp_path: Path) -> None:
    path = stand_manifest(tmp_path / "stand")
    await set_password(path)
    store = ProfileStore(tmp_path / "laptop")

    async with serving_stand(path) as (stand, _entrance):
        invite = json_of(await stand.entrance("invite", "--device", "laptop", "--json"))
        order = EnrolmentOrder(invite["url"], None, _HIVE, "laptop", "default")
        with pytest.raises(LandingRefusedError) as refused:
            await enrol_device(store, order, SystemClock())

    assert refused.value.status == 403
    assert store.names() == ()


@pytest.mark.parametrize(
    ("code", "hive_id", "said"),
    [
        (None, _HIVE, "No invite code"),
        (_CODE, None, "No Hive id"),
        ("not-a-code", _HIVE, "not one"),
        (_CODE, "device_01J8Z3Q4X5Y6Z7A8B9C0D1E2F3", "is not a Hive id"),
    ],
)
async def test_what_is_missing_or_malformed_is_refused_before_anything_is_sent(
    tmp_path: Path, code: str | None, hive_id: str | None, said: str
) -> None:
    order = EnrolmentOrder("http://127.0.0.1:9", code, hive_id, "laptop", "default")

    with pytest.raises(LandingError, match=said) as refused:
        await enrol_device(ProfileStore(tmp_path), order, SystemClock())

    assert "not-a-code" not in str(refused.value)


async def test_a_profile_already_in_use_is_never_overwritten(tmp_path: Path) -> None:
    store = ProfileStore(tmp_path)
    (tmp_path / "profiles").mkdir()
    (tmp_path / "profiles" / "default.json").write_text("{}", encoding="utf-8")
    order = EnrolmentOrder("http://127.0.0.1:9", _CODE, _HIVE, "laptop", "default")

    with pytest.raises(LandingError, match="already holds a device"):
        await enrol_device(store, order, SystemClock())
