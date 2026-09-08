"""Tests for waggle.errors: WaggleError's root, the protocol error tree and its stable codes.

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Exercises waggle.errors in isolation: the shape
    of the tree (which class subclasses which), and the code every class carries, which is the
    string an ErrorMessage puts on the wire and so must never collide or change.

Key invariants:
    - None: this module holds tests only.

See Also:
    - waggle.errors for the module under test.
"""

from __future__ import annotations

import re

import pytest

from waggle import errors
from waggle.errors import (
    CodecError,
    ConnectFailedError,
    ConnectionLostError,
    FrameTooLargeError,
    InvalidIdError,
    InvalidSignatureError,
    MalformedFrameError,
    MissingSignatureError,
    OutboxCorruptError,
    OutboxError,
    SignatureError,
    TransportClosedError,
    TransportError,
    UnknownKindError,
    UnknownSignerError,
    UnsupportedVersionError,
    WaggleError,
)

# The complete tree as (class, direct parent): the codec, signature, transport and outbox
# families each hang off their own family root, and every family root hangs directly off
# WaggleError. A class whose parent changes, or a new class missing from this table, fails below.
_TREE: list[tuple[type[WaggleError], type[WaggleError]]] = [
    (InvalidIdError, WaggleError),
    (CodecError, WaggleError),
    (MalformedFrameError, CodecError),
    (FrameTooLargeError, CodecError),
    (UnsupportedVersionError, CodecError),
    (UnknownKindError, CodecError),
    (SignatureError, WaggleError),
    (MissingSignatureError, SignatureError),
    (UnknownSignerError, SignatureError),
    (InvalidSignatureError, SignatureError),
    (TransportError, WaggleError),
    (TransportClosedError, TransportError),
    (ConnectionLostError, TransportError),
    (ConnectFailedError, TransportError),
    (OutboxError, WaggleError),
    (OutboxCorruptError, OutboxError),
]

# The wire-facing code table. These strings are what ErrorMessage.code carries to a peer, so a
# renamed code is a protocol change: this table is the tripwire that makes one deliberate.
_CODES: list[tuple[type[WaggleError], str]] = [
    (WaggleError, "waggle.error"),
    (InvalidIdError, "waggle.ids.invalid"),
    (CodecError, "waggle.codec.error"),
    (MalformedFrameError, "waggle.codec.malformed"),
    (FrameTooLargeError, "waggle.codec.too_large"),
    (UnsupportedVersionError, "waggle.version.unsupported_major"),
    (UnknownKindError, "waggle.codec.unknown_kind"),
    (SignatureError, "waggle.signature.error"),
    (MissingSignatureError, "waggle.signature.missing"),
    (UnknownSignerError, "waggle.signature.unknown_node"),
    (InvalidSignatureError, "waggle.signature.invalid"),
    (TransportError, "waggle.transport.error"),
    (TransportClosedError, "waggle.transport.closed"),
    (ConnectionLostError, "waggle.transport.connection_lost"),
    (ConnectFailedError, "waggle.transport.connect_failed"),
    (OutboxError, "waggle.outbox.error"),
    (OutboxCorruptError, "waggle.outbox.corrupt"),
]

# Every WaggleError subclass the module defines, discovered rather than listed, so a class added
# to errors.py but forgotten in _TREE/_CODES above still gets the generic checks.
_ALL_ERROR_CLASSES: list[type[WaggleError]] = [
    obj for obj in vars(errors).values() if isinstance(obj, type) and issubclass(obj, WaggleError)
]

# Lowercase words joined by dots, at least one segment after the "waggle." prefix.
_CODE_PATTERN = re.compile(r"^waggle(\.[a-z]+(_[a-z]+)*)+$")


def test_invalid_id_error_is_a_waggle_error() -> None:
    error = InvalidIdError("bad id.")

    assert isinstance(error, WaggleError)


def test_waggle_error_inherits_only_from_exception() -> None:
    # codingrules section 10: waggle has its own root because it cannot import hivemind, so
    # WaggleError must never end up inheriting from HiveMindError, directly or indirectly.
    assert WaggleError.__bases__ == (Exception,)


def test_waggle_error_carries_its_message() -> None:
    error = WaggleError("something went wrong.")

    assert str(error) == "something went wrong."


@pytest.mark.parametrize(("child", "parent"), _TREE)
def test_error_class_has_exactly_the_parent_the_brief_gives_it(
    child: type[WaggleError], parent: type[WaggleError]
) -> None:
    assert child.__bases__ == (parent,)


@pytest.mark.parametrize(("error_class", "expected_code"), _CODES)
def test_error_class_carries_its_wire_code(
    error_class: type[WaggleError], expected_code: str
) -> None:
    assert error_class.code == expected_code


@pytest.mark.parametrize("error_class", _ALL_ERROR_CLASSES)
def test_every_error_class_declares_its_own_code(error_class: type[WaggleError]) -> None:
    # Inheriting the parent's code silently would make two failures indistinguishable on the
    # wire, so every class must set `code` in its own body rather than fall through to its base.
    assert "code" in vars(error_class)


@pytest.mark.parametrize("error_class", _ALL_ERROR_CLASSES)
def test_every_error_code_is_lowercase_dotted_and_waggle_prefixed(
    error_class: type[WaggleError],
) -> None:
    assert _CODE_PATTERN.match(error_class.code), error_class.code


def test_error_codes_are_unique_across_the_tree() -> None:
    codes = [error_class.code for error_class in _ALL_ERROR_CLASSES]

    assert len(set(codes)) == len(codes)


def test_every_error_class_is_exported_and_listed_in_the_tree() -> None:
    # Discovery (vars) and declaration (__all__, _TREE, _CODES) must agree, in both directions,
    # so nothing is reachable that the public API and this test's tables do not know about.
    discovered = {error_class.__name__ for error_class in _ALL_ERROR_CLASSES}
    in_tree = {WaggleError.__name__} | {child.__name__ for child, _ in _TREE}
    in_codes = {error_class.__name__ for error_class, _ in _CODES}

    assert discovered == set(errors.__all__)
    assert discovered == in_tree
    assert discovered == in_codes
