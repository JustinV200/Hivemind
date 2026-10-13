"""Test hivemind.entrance.images.door: every refusal before the deposit, then C2 Nectar.

Over real listeners and a real Queen, with the rig's in-memory Nectar seam to read back: an image
that is not PNG, JPEG or WebP, is larger than ``max_image_bytes``, is empty or does not begin as
its label says, or comes from a device over its byte budget, a device without ``entrance:submit``
or a device that has no session is refused, and in every case nothing is deposited and the
device's budget is left whole. An accepted image is kept as ``C2`` Nectar with where it came from
(the device, when, its caption and task) and the retention window that sweeps it away; its bytes
and caption never show in a repr.

Fits into the Hive:
    Mirrors src/hivemind/entrance/images/door.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from builders.entrance.images import UPLOADER, deposit, image_rig, jpeg, png, webp
from builders.entrance.landing import LandingSession
from builders.entrance.serving import ProgramGrant, serving

from hivemind.cell import HoneyClearance
from hivemind.entrance.images import DepositParams, Upload
from hivemind.entrance.voice import KeptImage
from waggle.ids import new_task_id

_LIMIT = 100  # A small max_image_bytes, so a test crosses it with a few bytes.


async def test_an_image_is_kept_as_c2_nectar_with_where_it_came_from() -> None:
    async with serving(image_rig()) as rig:
        client, session = await rig.program(UPLOADER)
        task = new_task_id(rig.clock)
        body = jpeg(300)

        kept_response = await deposit(
            client, session, body, "image/jpeg", caption="The back fence", task_id=task
        )

        [kept] = rig.nectar.kept
    assert kept_response.status_code == 202, kept_response.text
    view = kept_response.json()
    assert isinstance(kept, KeptImage)
    assert kept.clearance is HoneyClearance.C2
    assert (kept.device_id, kept.media_type, kept.data) == (
        session.key.device_id,
        "image/jpeg",
        body,
    )
    assert (kept.caption, kept.task_id, kept.size) == ("The back fence", task, 300)
    # The window is the manifest's image_keep_hours (a day by default), counted from the deposit.
    assert kept.expires_at - kept.kept_at == timedelta(hours=24)
    assert (view["nectar_id"], view["size_bytes"], view["media_type"]) == (
        kept.ref,
        300,
        "image/jpeg",
    )
    assert kept.ref.startswith("nectar_")
    assert "back fence" not in repr(kept) and "\\xff" not in repr(kept)


@pytest.mark.parametrize(
    ("body", "label"),
    [
        (png(), "image/png"),
        (jpeg(), "image/jpeg"),
        (webp(), "image/webp"),
        (png(), "Image/PNG; charset=binary"),
    ],
    ids=["png", "jpeg", "webp", "label-case-and-parameters"],
)
async def test_each_accepted_format_is_kept(body: bytes, label: str) -> None:
    async with serving(image_rig()) as rig:
        client, session = await rig.program(UPLOADER)

        accepted = await deposit(client, session, body, label)

        assert accepted.status_code == 202, accepted.text
        assert [item.data for item in rig.nectar.kept] == [body]


@pytest.mark.parametrize(
    ("body", "label", "status", "error"),
    [
        (png(), None, 415, "hivemind.entrance.image_type_refused"),
        (png(), "image/gif", 415, "hivemind.entrance.image_type_refused"),
        (b"%PDF-1.7", "application/pdf", 415, "hivemind.entrance.image_type_refused"),
        (png(_LIMIT + 1), "image/png", 413, "hivemind.entrance.image_too_large"),
        (b"", "image/png", 422, "hivemind.entrance.image_malformed"),
        (jpeg(), "image/png", 422, "hivemind.entrance.image_malformed"),
        (b"RIFF" + bytes(4) + b"WAVE", "image/webp", 422, "hivemind.entrance.image_malformed"),
    ],
    ids=["no-label", "gif", "pdf", "too-large", "empty", "jpeg-as-png", "wav-as-webp"],
)
async def test_an_image_the_hive_cannot_take_is_refused_and_costs_the_device_nothing(
    body: bytes, label: str | None, status: int, error: str
) -> None:
    # The budget holds exactly one largest image: a refusal that charged anything would show.
    async with serving(image_rig(max_image_bytes=_LIMIT, image_bytes_per_minute=_LIMIT)) as rig:
        client, session = await rig.program(UPLOADER)

        refused = await deposit(client, session, body, label)
        nothing_kept = rig.nectar.kept
        whole = await deposit(client, session, png(_LIMIT))

    assert refused.status_code == status, refused.text
    assert refused.json()["error"] == error
    assert nothing_kept == ()
    assert whole.status_code == 202, whole.text


async def test_an_image_exactly_max_image_bytes_is_taken_and_one_byte_more_is_not() -> None:
    async with serving(image_rig(max_image_bytes=_LIMIT)) as rig:
        client, session = await rig.program(UPLOADER)

        at_the_limit = await deposit(client, session, png(_LIMIT))
        over = await deposit(client, session, png(_LIMIT + 1))

    assert at_the_limit.status_code == 202, at_the_limit.text
    assert over.status_code == 413
    assert f"{_LIMIT + 1} bytes" in over.json()["detail"]
    assert f"at most {_LIMIT}" in over.json()["detail"]


async def test_the_byte_budget_counts_each_devices_images_whole() -> None:
    options = image_rig(max_image_bytes=_LIMIT, image_bytes_per_minute=150)
    async with serving(options) as rig:
        first, first_session = await rig.program(UPLOADER)
        other, other_session = await rig.program(UPLOADER)

        sent = await deposit(first, first_session, png(100))
        over = await deposit(first, first_session, png(100))
        fits = await deposit(first, first_session, png(50))  # Only 50 left: the refusal took none.
        elsewhere = await deposit(other, other_session, png(100))
        kept = rig.nectar.kept

    assert [response.status_code for response in (sent, over, fits, elsewhere)] == [
        202,
        429,
        202,
        202,
    ]
    assert over.json()["error"] == "hivemind.entrance.image_over_budget"
    assert [len(item.data) for item in kept] == [100, 50, 100]


async def test_a_device_without_entrance_submit_is_refused_and_nothing_is_kept() -> None:
    async with serving(image_rig()) as rig:
        client, session = await rig.program(ProgramGrant(capabilities=("observe",)))

        refused = await deposit(client, session, png())
        kept = rig.nectar.kept

    assert refused.status_code == 403, refused.text
    assert kept == ()


async def test_a_device_with_no_session_is_refused_and_nothing_is_kept() -> None:
    async with serving(image_rig()) as rig:
        client, session = await rig.program(UPLOADER)

        refused = await deposit(client, LandingSession("made-up-token", session.key), png())
        kept = rig.nectar.kept

    assert refused.status_code == 401
    assert kept == ()


async def test_a_deposited_image_is_swept_away_when_its_window_ends() -> None:
    async with serving(image_rig(image_keep_hours=1.0)) as rig:
        client, session = await rig.program(UPLOADER)
        await deposit(client, session, png())
        [kept] = rig.nectar.kept

        early = await rig.nectar.sweep(kept.kept_at + timedelta(minutes=59))
        late = await rig.nectar.sweep(kept.kept_at + timedelta(hours=1, seconds=1))
        remaining = rig.nectar.kept

    assert (early, late, remaining) == (0, 1, ())


def test_neither_the_bytes_nor_the_caption_show_in_a_repr() -> None:
    params = DepositParams(caption="my home address")
    upload = Upload(png(32), "image/png", params)

    shown = repr(upload) + repr(params)

    assert "address" not in shown
    assert "PNG" not in shown and r"\x89" not in shown
