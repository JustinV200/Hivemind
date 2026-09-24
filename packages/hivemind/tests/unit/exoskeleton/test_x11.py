"""Unit tests for hivemind.exoskeleton.x11: X11Display and the wildcard authority file."""

from __future__ import annotations

import struct
from pathlib import Path

import pytest

from hivemind.exoskeleton.geometry import ScreenSize
from hivemind.exoskeleton.x11 import COOKIE_BYTES, X11Display, authority_file

_SIZE = ScreenSize(640, 480)


@pytest.mark.parametrize("name", [":0", ":12", ":3.1"])
def test_a_local_display_name_is_accepted(name: str) -> None:
    assert X11Display(name=name, authority=None, size=_SIZE).name == name


@pytest.mark.parametrize("name", ["", "localhost:10.0", "host:0", ":", ":0; rm -rf /", "0"])
def test_anything_but_a_local_display_name_is_refused(name: str) -> None:
    with pytest.raises(ValueError, match="local only"):
        X11Display(name=name, authority=None, size=_SIZE)


def test_environment_sets_xauthority_only_for_a_display_with_its_own_cookie(tmp_path: Path) -> None:
    lease = X11Display(name=":5", authority=tmp_path / "cookie", size=_SIZE)
    borrowed = X11Display(name=":0", authority=None, size=_SIZE)

    assert lease.environment() == {"DISPLAY": ":5", "XAUTHORITY": str(tmp_path / "cookie")}
    assert borrowed.environment() == {"DISPLAY": ":0"}


def test_authority_file_is_one_wildcard_entry_for_any_display_number() -> None:
    cookie = bytes(range(COOKIE_BYTES))

    data = authority_file(cookie)

    # family, then four length-prefixed fields: address, display number, protocol, cookie.
    (family,) = struct.unpack(">H", data[:2])
    fields, offset = [], 2
    while offset < len(data):
        (length,) = struct.unpack(">H", data[offset : offset + 2])
        fields.append(data[offset + 2 : offset + 2 + length])
        offset += 2 + length
    assert family == 0xFFFF
    assert fields == [b"", b"", b"MIT-MAGIC-COOKIE-1", cookie]


def test_authority_file_refuses_a_cookie_of_the_wrong_length() -> None:
    with pytest.raises(ValueError, match="cookie"):
        authority_file(b"short")
