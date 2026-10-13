"""Tests for hivemind.swarm.enrolment.invite: the token and the string that carries it.

Fits into the Hive:
    Mirrors src/hivemind/swarm/enrolment/invite.py (codingrules section 3: tests/unit mirrors src/
    one-to-one). The format is also held to the worked example in docs/waggle/enrolment.md section
    3.3: the packet's own reader is written from that document, so the document's string must
    decode, and re-encode, byte for byte.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.swarm.enrolment.invite for the module under test.
    - docs/waggle/enrolment.md section 3.3 for the format.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
from datetime import UTC, datetime
from pathlib import Path

import pytest
from builders.swarm import make_endpoint, make_hive_party
from pydantic import ValidationError

from hivemind.swarm.enrolment import (
    INVITE_CODE_PATTERN,
    INVITE_PREFIX,
    InviteEndpoint,
    build_invite_string,
    new_invite_token,
    parse_invite_string,
    token_hash,
)
from hivemind.swarm.enrolment.invite import MAX_INVITE_STRING_CHARS
from hivemind.swarm.errors import InviteFormatError

_DOC = Path(__file__).resolve().parents[6] / "docs" / "waggle" / "enrolment.md"
_KEYS = ["token", "url", "hive_id", "hive_node_id", "hive_key", "tls_sha256", "expires_at", "label"]
_PIN = "42b341bef73acad9d5dcb54454a9afe505b22326a9e0441b6a3aab1a3d719195"
_EXPIRES = datetime(2026, 10, 13, 12, 15, tzinfo=UTC)
_HIVE = make_hive_party()


def _encode(document: object) -> str:
    """Wrap any JSON-able value as an invite string would carry it."""
    raw = json.dumps(document, separators=(",", ":")).encode("utf-8")
    return INVITE_PREFIX + base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _eight_keys(**changes: object) -> dict[str, object]:
    """The eight keys of a valid invite document, with `changes` applied."""
    document: dict[str, object] = {
        "token": new_invite_token(),
        "url": "ws://127.0.0.1:8720",
        "hive_id": _HIVE.hive_id,
        "hive_node_id": _HIVE.node_id,
        "hive_key": _HIVE.public_hex,
        "tls_sha256": None,
        "expires_at": _EXPIRES.isoformat(),
        "label": None,
    }
    document.update(changes)
    return document


def _doc_example() -> tuple[str, dict[str, object]]:
    """The worked example of enrolment.md section 3.3: the wrapped string and what it decodes to."""
    text = _DOC.read_text(encoding="utf-8")
    string = re.search(r"```text\n(pollen1\.[A-Za-z0-9_\-\n]+)```", text)
    decoded = re.search(r"It decodes to:\n\n```json\n(\{.*?\})\n```", text, re.DOTALL)
    assert string is not None and decoded is not None
    return "".join(string.group(1).split()), json.loads(decoded.group(1))


def test_a_token_is_26_base32_characters_in_hyphenated_groups_of_four() -> None:
    token = new_invite_token()

    assert re.fullmatch(INVITE_CODE_PATTERN, token) and len(token) == 32
    assert token.count("-") == 6


def test_tokens_do_not_repeat() -> None:
    assert len({new_invite_token() for _ in range(200)}) == 200


def test_the_stored_hash_is_the_sha256_of_the_token_as_it_travels() -> None:
    token = new_invite_token()

    assert token_hash(token) == hashlib.sha256(token.encode("utf-8")).hexdigest()
    assert token_hash(token) != token_hash(new_invite_token())


@pytest.mark.parametrize("label", [None, "kitchen-pi"])
@pytest.mark.parametrize(
    ("url", "pin"), [("ws://127.0.0.1:8720", None), ("wss://hive.example.net:8720", _PIN)]
)
def test_a_built_string_parses_back_to_what_it_was_built_from(
    label: str | None, url: str, pin: str | None
) -> None:
    token, endpoint = new_invite_token(), make_endpoint(_HIVE, url, pin)

    contents = parse_invite_string(build_invite_string(token, endpoint, _EXPIRES, label))

    assert (contents.token, contents.endpoint) == (token, endpoint)
    assert (contents.expires_at, contents.label) == (_EXPIRES, label)


def test_the_string_carries_exactly_the_eight_keys_in_order_with_no_padding() -> None:
    text = build_invite_string(new_invite_token(), make_endpoint(_HIVE), _EXPIRES, "desk")

    body = text.removeprefix(INVITE_PREFIX)
    document = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))

    assert text.startswith(INVITE_PREFIX) and "=" not in text
    assert list(document) == _KEYS


def test_the_document_example_decodes_to_the_values_beside_it() -> None:
    string, decoded = _doc_example()

    contents = parse_invite_string(string)

    assert contents.token == decoded["token"]
    assert contents.endpoint.url == decoded["url"]
    assert contents.endpoint.hive_id == decoded["hive_id"]
    assert contents.endpoint.hive_node_id == decoded["hive_node_id"]
    assert contents.endpoint.hive_key_hex == decoded["hive_key"]
    assert contents.endpoint.tls_sha256 == decoded["tls_sha256"]
    assert contents.expires_at == datetime(2026, 10, 13, 12, 15, tzinfo=UTC)
    assert contents.label == decoded["label"]


def test_building_the_document_example_reproduces_its_string_exactly() -> None:
    string, _decoded = _doc_example()
    contents = parse_invite_string(string)

    rebuilt = build_invite_string(
        contents.token, contents.endpoint, contents.expires_at, contents.label
    )

    assert rebuilt == string


def test_padding_and_line_breaks_are_accepted_when_reading() -> None:
    text = build_invite_string(new_invite_token(), make_endpoint(_HIVE), _EXPIRES, None)
    padded = text + "=" * (-len(text.removeprefix(INVITE_PREFIX)) % 4)
    wrapped = "\n".join(padded[i : i + 40] for i in range(0, len(padded), 40))

    assert parse_invite_string(wrapped) == parse_invite_string(text)


@pytest.mark.parametrize("text", ["", "pollen2.e30", "token", "POLLEN1.e30", " pollen0.abc"])
def test_a_string_with_another_prefix_is_not_an_invite(text: str) -> None:
    with pytest.raises(InviteFormatError):
        parse_invite_string(text)


def test_a_string_longer_than_any_real_invite_is_refused_unread() -> None:
    with pytest.raises(InviteFormatError):
        parse_invite_string(INVITE_PREFIX + "A" * MAX_INVITE_STRING_CHARS)


@pytest.mark.parametrize(
    "document",
    [
        [1, 2],
        "text",
        {k: v for k, v in _eight_keys().items() if k != "label"},
        {**_eight_keys(), "extra": 1},
        {**_eight_keys(), "token": "NOT-A-TOKEN"},
        _eight_keys(hive_id="hive_x"),
        _eight_keys(hive_key="abc"),
        _eight_keys(url="http://127.0.0.1:8720"),
        _eight_keys(url="wss://hive.example.net:8720"),
        _eight_keys(tls_sha256=_PIN),
        _eight_keys(expires_at="soon"),
        _eight_keys(label="x" * 65),
    ],
    ids=[
        "list",
        "text",
        "missing-key",
        "extra-key",
        "bad-token",
        "bad-hive-id",
        "bad-key",
        "bad-scheme",
        "wss-without-pin",
        "ws-with-pin",
        "bad-time",
        "long-label",
    ],
)
def test_a_document_that_breaks_the_format_is_refused(document: object) -> None:
    with pytest.raises(InviteFormatError):
        parse_invite_string(_encode(document))


@pytest.mark.parametrize(
    "body", ["!!!not-base64!!!", base64.urlsafe_b64encode(b"\xff\xfe").decode()]
)
def test_text_that_is_not_base64_json_is_refused(body: str) -> None:
    with pytest.raises(InviteFormatError):
        parse_invite_string(INVITE_PREFIX + body)


def test_a_refusal_never_repeats_the_text_it_read() -> None:
    marker = "MARKER-ZQXW"

    with pytest.raises(InviteFormatError) as caught:
        parse_invite_string(_encode(_eight_keys(token=marker)))

    assert marker not in str(caught.value) and marker not in repr(caught.value)


def test_the_parsed_contents_do_not_show_the_token_when_printed() -> None:
    token = new_invite_token()
    contents = parse_invite_string(build_invite_string(token, make_endpoint(_HIVE), _EXPIRES, None))

    assert token not in repr(contents)


def test_an_endpoint_needs_a_pin_exactly_for_wss() -> None:
    with pytest.raises(ValidationError):
        InviteEndpoint(
            url="wss://hive.example.net:8720",
            hive_id=_HIVE.hive_id,
            hive_node_id=_HIVE.node_id,
            hive_key_hex=_HIVE.public_hex,
        )
    with pytest.raises(ValidationError):
        make_endpoint(_HIVE, "ws://127.0.0.1:8720", _PIN)


def test_a_plaintext_endpoint_must_name_a_loopback_host() -> None:
    with pytest.raises(ValidationError):
        make_endpoint(_HIVE, "ws://192.168.1.10:8720")


def test_an_endpoint_round_trips_through_json() -> None:
    endpoint = make_endpoint(_HIVE, "wss://hive.example.net:8720", _PIN)

    assert InviteEndpoint.model_validate_json(endpoint.model_dump_json()) == endpoint
