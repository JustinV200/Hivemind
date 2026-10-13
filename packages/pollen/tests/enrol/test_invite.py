"""Test reading a ``pollen1.`` invite: what is accepted, what is refused, what is never quoted.

An operator gives the Pollen Packet (the thin gateway program on a device the Hive borrows) an
invite string: ``pollen1.`` and the URL-safe base64 of a JSON object with exactly eight keys
(enrolment.md section 3.3). The packet refuses anything it cannot be sure of: another prefix,
damaged base64 or JSON, a missing or extra key, a ``wss://`` address with nothing to pin, a
``ws://`` address that is not loopback, a fingerprint on a plain link, a malformed id, or an
invite that has already expired.
The string and the token inside it are secrets until used, so no error message quotes either.

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Tests pollen.enrol.invite.

Key invariants:
    - Every refusal is an ``InviteError`` whose message names the rule, never the text.
    - ``Invite`` round-trips through its JSON form and rejects what the string rules reject.

See Also:
    - docs/waggle/enrolment.md sections 3.3 and 4.
"""

from __future__ import annotations

import base64
import json
from datetime import UTC, datetime, timedelta

import pytest

from pollen.enrol import Invite, parse_invite
from pollen.errors import InviteError

INVITE_CODE = "H7QK-3MZP-WD2F-XNVA-R5TG-C4YB-J6"
BODY = {
    "token": INVITE_CODE,
    "url": "wss://hive.example.net:8720",
    "hive_id": "hive_01M4TJ4GG0248H248H248H248J",
    "hive_node_id": "node_01M4TJ4GG0248H248H248H248H",
    "hive_key": "03a107bff3ce10be1d70dd18e74bc09967e4d6309ba50d5f1ddc8664125531b8",
    "tls_sha256": "42b341bef73acad9d5dcb54454a9afe505b22326a9e0441b6a3aab1a3d719195",
    "expires_at": "2026-10-13T12:15:00Z",
    "label": "kitchen-pi",
}


def _string(body: dict[str, object] | None = None, *, pad: bool = False) -> str:
    """Encode an invite body the way the Hive Stand does."""
    packed = json.dumps(BODY if body is None else body, separators=(",", ":")).encode()
    text = base64.urlsafe_b64encode(packed).decode()
    return "pollen1." + (text if pad else text.rstrip("="))


def _with(**changes: object) -> dict[str, object]:
    """Return the standard body with some keys replaced."""
    return {**BODY, **changes}


@pytest.fixture
def now() -> datetime:
    """A moment fifteen minutes before the standard invite expires."""
    return datetime(2026, 10, 13, 12, 0, tzinfo=UTC)


def test_a_valid_invite_is_read(now: datetime) -> None:
    invite = parse_invite(_string(), now)
    assert (invite.url, invite.hive_id, invite.label) == (
        BODY["url"],
        BODY["hive_id"],
        "kitchen-pi",
    )
    assert invite.tls_sha256 == BODY["tls_sha256"]
    assert INVITE_CODE not in repr(invite)


def test_padding_and_surrounding_whitespace_are_accepted(now: datetime) -> None:
    padded = parse_invite(f"  {_string(pad=True)}\n", now)
    assert padded == parse_invite(_string(), now)


def test_a_loopback_invite_without_a_fingerprint_is_valid(now: datetime) -> None:
    body = _with(url="ws://127.0.0.1:8720", tls_sha256=None, label=None)
    assert parse_invite(_string(body), now).tls_sha256 is None


def test_the_invite_model_round_trips() -> None:
    invite = Invite.model_validate(BODY)
    assert Invite.model_validate_json(invite.model_dump_json()) == invite


@pytest.mark.parametrize(
    "text",
    [
        "",
        "pollen2." + _string()[len("pollen1.") :],
        _string()[len("pollen1.") :],
        "pollen1." + "A" * 5000,
        "pollen1.!!!not-base64!!!",
        "pollen1." + base64.urlsafe_b64encode(b"not json").decode(),
        "pollen1." + base64.urlsafe_b64encode(b"[1, 2]").decode(),
        "pollen1." + base64.urlsafe_b64encode(b"[" * 3000).decode(),
    ],
)
def test_a_damaged_or_foreign_string_is_refused_without_quoting_it(
    text: str, now: datetime
) -> None:
    with pytest.raises(InviteError) as raised:
        parse_invite(text, now)
    assert text[8:40] not in str(raised.value) or not text[8:40]


@pytest.mark.parametrize(
    ("body", "field"),
    [
        ({k: v for k, v in BODY.items() if k != "token"}, "token"),
        ({**BODY, "extra": 1}, "extra"),
        (_with(hive_id="warden_01M4TJ4GG0248H248H248H248J"), "hive_id"),
        (_with(hive_key="xyz"), "hive_key"),
        ({**BODY, "token": "short"}, "token"),
        (_with(tls_sha256=None), "invite"),
        (_with(url="ws://127.0.0.1:8720"), "invite"),
        (_with(url="ws://hive.example.net:8720", tls_sha256=None), "invite"),
    ],
)
def test_an_invite_that_breaks_a_rule_names_the_field_and_not_the_token(
    body: dict[str, object], field: str, now: datetime
) -> None:
    with pytest.raises(InviteError) as raised:
        parse_invite(_string(body), now)
    assert field in str(raised.value)
    assert INVITE_CODE not in str(raised.value) and "hive.example.net" not in str(raised.value)


def test_an_expired_invite_is_refused(now: datetime) -> None:
    later = now + timedelta(hours=1)
    with pytest.raises(InviteError, match="expired"):
        parse_invite(_string(), later)
