"""Test hivemind.entrance.android.links: /.well-known/assetlinks.json on both listeners.

The Digital Asset Links file is static and unauthenticated, per Google's format, with the two
delegate permissions the app needs (App Links and the Credential Manager). It is served only while
``[entrance.android]`` is configured, on both listeners; otherwise the path is a 404 like any
other. It is not a ``/v1/`` route and is not in the committed Landing Board document.

Fits into the Hive:
    Mirrors src/hivemind/entrance/android/links.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.
"""

from __future__ import annotations

import json

from builders.entrance.images import FINGERPRINT, PACKAGE, android_rig
from builders.entrance.serving import RigOptions, serving

from hivemind.entrance.android import ASSET_LINKS_PATH, render_asset_links
from hivemind.entrance.landing_board import render_document
from hivemind.manifest.schema.entrance import EntranceAndroidSection

_OTHER = ":".join(["AB"] * 32)  # A second signing certificate (a debug key beside the release).
_EXPECTED = [
    {
        "relation": [
            "delegate_permission/common.handle_all_urls",
            "delegate_permission/common.get_login_creds",
        ],
        "target": {
            "namespace": "android_app",
            "package_name": PACKAGE,
            "sha256_cert_fingerprints": [FINGERPRINT],
        },
    }
]


def test_the_document_is_googles_statement_list_for_the_configured_app() -> None:
    section = EntranceAndroidSection(package_name=PACKAGE, cert_fingerprints=(FINGERPRINT,))

    document = render_asset_links(section)

    assert document is not None
    assert json.loads(document) == _EXPECTED


def test_every_configured_fingerprint_is_listed_in_order() -> None:
    section = EntranceAndroidSection(
        package_name=PACKAGE, cert_fingerprints=(FINGERPRINT, _OTHER.lower())
    )

    document = render_asset_links(section)

    assert document is not None
    [statement] = json.loads(document)
    assert statement["target"]["sha256_cert_fingerprints"] == [FINGERPRINT, _OTHER]


def test_an_unconfigured_section_renders_no_document() -> None:
    assert render_asset_links(EntranceAndroidSection()) is None


async def test_both_listeners_serve_the_file_unauthenticated_as_json() -> None:
    async with serving(android_rig()) as rig:
        answers = [
            await rig.client(remote=side).http.get(ASSET_LINKS_PATH) for side in (False, True)
        ]

    for answer in answers:
        assert answer.status_code == 200, answer.text
        assert answer.headers["content-type"].startswith("application/json")
        assert answer.json() == _EXPECTED
        assert "max-age" in answer.headers["cache-control"]
        assert "content-security-policy" in answer.headers


async def test_an_unconfigured_hive_answers_a_404_on_both_listeners() -> None:
    async with serving(RigOptions(remote=True)) as rig:
        answers = [
            await rig.client(remote=side).http.get(ASSET_LINKS_PATH) for side in (False, True)
        ]

    assert [answer.status_code for answer in answers] == [404, 404]


async def test_the_file_is_static_whatever_the_request_carries() -> None:
    async with serving(android_rig()) as rig:
        http = rig.client(remote=True).http

        first = await http.get(ASSET_LINKS_PATH)
        again = await http.get(ASSET_LINKS_PATH, headers={"Authorization": "Bearer nonsense"})

    assert first.content == again.content


def test_the_file_is_outside_the_landing_board_document() -> None:
    document = json.loads(render_document())

    assert ASSET_LINKS_PATH not in document["paths"]
    assert all(path.startswith("/v1/") for path in document["paths"])
