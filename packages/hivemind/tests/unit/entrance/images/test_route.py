"""Test hivemind.entrance.images.route: POST /v1/nectar/images over real listeners.

An image arrives as a raw body with its media type (ADR-0040): the Landing Board answers 202 with
where it was kept and nothing of what it shows, takes a body far larger than any JSON body, serves
both listeners, and refuses a query it does not define. The committed contract describes the row:
its three media types, its allowance, its capability (``entrance:submit`` and no C2, since the
answer holds no content) and the refusals only an image can be answered with.

Fits into the Hive:
    Mirrors src/hivemind/entrance/images/route.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.
"""

from __future__ import annotations

import json

import pytest
from builders.entrance.images import IMAGES, UPLOADER, deposit, image_rig, png
from builders.entrance.serving import ProgramGrant, RigOptions, serving

from hivemind.entrance.images import IMAGE_BODY, IMAGE_ROUTES, ImageMediaType
from hivemind.entrance.landing_board import render_document
from hivemind.manifest.schema.entrance import IMAGE_BYTES_CEILING

_JSON_BODY_LIMIT = 262_144  # What every JSON body is held to; an image is far larger.


async def test_the_answer_says_where_the_image_is_and_nothing_of_what_it_shows() -> None:
    async with serving(image_rig()) as rig:
        client, session = await rig.program(UPLOADER)

        accepted = await deposit(client, session, png(2_048), caption="Receipt")

    assert accepted.status_code == 202, accepted.text
    body = accepted.json()
    assert set(body) == {"nectar_id", "size_bytes", "media_type", "deposited_at", "expires_at"}
    assert (body["size_bytes"], body["media_type"]) == (2_048, "image/png")
    assert "Receipt" not in accepted.text


async def test_both_listeners_take_an_image() -> None:
    async with serving(RigOptions(remote=True)) as rig:
        local, local_session = await rig.program(UPLOADER)
        far, far_session = await rig.program(
            ProgramGrant(capabilities=UPLOADER.capabilities, remote=True)
        )

        on_loopback = await deposit(local, local_session, png())
        on_remote = await deposit(far, far_session, png())

    assert (on_loopback.status_code, on_remote.status_code) == (202, 202)


async def test_an_image_larger_than_any_json_body_is_still_read_whole() -> None:
    big = png(_JSON_BODY_LIMIT + 4_096)
    async with serving(image_rig()) as rig:
        client, session = await rig.program(UPLOADER)

        accepted = await deposit(client, session, big)
        [kept] = rig.nectar.kept

    assert accepted.status_code == 202, accepted.text
    assert len(kept.data) == len(big)


async def test_a_body_past_the_ceiling_is_refused_by_the_body_limit_whatever_the_manifest() -> None:
    async with serving(image_rig(max_image_bytes=IMAGE_BYTES_CEILING)) as rig:
        client, session = await rig.program(UPLOADER)

        refused = await deposit(client, session, png(IMAGE_BYTES_CEILING + 1))

        assert refused.status_code == 413
        assert rig.nectar.kept == ()


@pytest.mark.parametrize(
    "query",
    [{"stray": "1"}, {"caption": ""}, {"caption": "x" * 281}, {"task_id": "nope"}],
    ids=["stray", "empty-caption", "long-caption", "bad-task"],
)
async def test_a_query_the_route_does_not_define_is_refused(query: dict[str, str]) -> None:
    async with serving(image_rig()) as rig:
        client, session = await rig.program(UPLOADER)

        refused = await deposit(client, session, png(), **query)
        kept = rig.nectar.kept

    assert refused.status_code == 422, refused.text
    assert refused.json()["error"] == "hivemind.entrance.invalid_request"
    assert kept == ()


def test_the_row_asks_for_entrance_submit_alone_and_takes_three_formats() -> None:
    [row] = IMAGE_ROUTES

    assert (row.method, row.path, row.status_code) == ("POST", IMAGES, 202)
    assert row.access.needs() == ("entrance:submit",)
    assert IMAGE_BODY.media_types == tuple(format_.value for format_ in ImageMediaType)
    assert IMAGE_BODY.max_bytes == IMAGE_BYTES_CEILING
    assert row.mounted_when is None


def test_the_committed_contract_describes_the_row() -> None:
    document = json.loads(render_document())

    operation = document["paths"][IMAGES]["post"]
    assert set(operation["requestBody"]["content"]) == {"image/png", "image/jpeg", "image/webp"}
    assert operation["x-hive-capability"] == "entrance:submit"
    assert operation["x-hive-c2"] is False
    assert operation["x-hive-body"]["max_bytes"] == IMAGE_BYTES_CEILING
    assert {"413", "415", "429"} <= set(operation["responses"])
    assert operation["responses"]["202"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/ImageDepositView"
    }
