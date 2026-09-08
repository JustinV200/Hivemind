"""Read a received frame: parse it, check its version and kind, and build the Envelope it carries.

Waggle is the Hive's bee-to-bee wire protocol (named after the honeybee waggle dance). A frame is
one JSON object whose keys are exactly the Envelope's (the outer wrapper every message travels
in) fields, and ``Codec.decode`` in ``waggle.codec`` turns one back into an Envelope by a fixed
sequence of checks: size, then the four key-free steps defined here (a well-formed object with
the envelope keys, a version whose major this node speaks, a registered kind, and finally the
payload validated with the registered class and the envelope validated around it), with the
signature check between the kind and the payload when a verifier is configured. The steps live
here rather than in ``waggle.codec`` so that module stays under the codingrules 5.1 size limit
and holds only the policy (sign, verify, size) while this one holds the pure reading; each step
raises the ``CodecError`` subclass whose stable code a transport reports, never a ``json`` or
pydantic error. Parsing is stricter than Python's ``json`` default: ``NaN`` and ``Infinity``
are not JSON (RFC 8259) and a frame carrying them is malformed, and a frame nested deeply
enough to exhaust the parser's recursion is malformed too, not a crash.

Fits into the Hive:
    Its own layer (used by every layer in hivemind and by pollen, the lightweight device
    connector), inside the waggle package. Called by waggle.codec.Codec.decode only; calls into
    waggle.envelope, waggle.messages.registry and waggle.errors. Latency class: microseconds,
    pure CPU.

Key invariants:
    - parse_frame returns a dict whose key set is exactly ENVELOPE_KEYS, or raises
      MalformedFrameError; nothing else inspects the frame's bytes.
    - check_version accepts any minor of PROTOCOL_MAJOR and rejects every other major with
      UnsupportedVersionError; a version that is not a "<major>.<minor>" string is malformed.
    - build_envelope never lets a pydantic ValidationError escape: both the payload and the
      envelope failures become InvalidPayloadError, whose message names locations, never values.

See Also:
    - waggle.codec for Codec.decode, which composes these steps in the spec's order.
    - docs/waggle/spec.md sections 4 (versioning), 5 (wire format) and 7 (error codes).
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping

from pydantic import ValidationError

from waggle.envelope import PROTOCOL_MAJOR, VERSION_PATTERN, Envelope
from waggle.errors import InvalidPayloadError, MalformedFrameError, UnsupportedVersionError
from waggle.messages.registry import model_for, spec_for

# The keys a frame must carry, exactly: the Envelope's fields (spec section 5).
ENVELOPE_KEYS = frozenset(Envelope.model_fields)

__all__ = ["ENVELOPE_KEYS", "build_envelope", "check_kind", "check_version", "parse_frame"]

_VERSION_RE = re.compile(VERSION_PATTERN)  # Compiled once; every frame is matched against it.


def parse_frame(frame: bytes) -> dict[str, object]:
    """Decode UTF-8 JSON to the one object shape a frame may have: exactly the envelope keys.

    Args:
        frame: The bytes exactly as received.

    Returns:
        The raw wire dict, keyed by exactly ENVELOPE_KEYS; values are still untyped JSON.

    Raises:
        MalformedFrameError: Not UTF-8, not JSON, a non-JSON constant (NaN, Infinity), nested
            past the parser's limit, not an object, or not exactly the envelope keys.
    """
    # UnicodeDecodeError and JSONDecodeError are both ValueErrors; RecursionError is what a
    # frame of a million nested brackets produces, and it is malformed, not a crash.
    try:
        parsed: object = json.loads(frame.decode("utf-8"), parse_constant=_reject_non_finite)
    except (ValueError, RecursionError) as exc:
        raise MalformedFrameError("The frame is not valid UTF-8 JSON.") from exc
    if not isinstance(parsed, dict):
        raise MalformedFrameError(
            f"The frame's top level is a JSON {type(parsed).__name__}, not the object an "
            "Envelope serialises to."
        )
    # Exactly the envelope keys: a missing one cannot be defaulted (a default is not what the
    # peer signed) and an extra one is a newer minor this node does not speak, or tampering.
    keys = frozenset(parsed)
    if keys != ENVELOPE_KEYS:
        missing = sorted(ENVELOPE_KEYS - keys)
        extra = sorted(str(key) for key in keys - ENVELOPE_KEYS)
        raise MalformedFrameError(
            f"The frame's keys are not the envelope's: missing {missing}, unknown {extra}."
        )
    return parsed


def check_version(raw: Mapping[str, object]) -> None:
    """Reject a version that is not a "<major>.<minor>" string, or whose major is unknown.

    Args:
        raw: The dict parse_frame returned.

    Raises:
        MalformedFrameError: ``version`` is not a string of the right shape.
        UnsupportedVersionError: The major is not PROTOCOL_MAJOR.
    """
    version = raw["version"]
    if not isinstance(version, str) or _VERSION_RE.fullmatch(version) is None:
        raise MalformedFrameError(
            f'The frame\'s version {version!r} is not a "<major>.<minor>" string.'
        )
    # Only the major matters: a breaking change bumps it and is rejected; any minor is accepted
    # because minor changes are additive (spec section 4).
    major = int(version.partition(".")[0])
    if major != PROTOCOL_MAJOR:
        raise UnsupportedVersionError(
            f"The frame speaks protocol version {version}, major {major}; this node speaks "
            f"major {PROTOCOL_MAJOR} only."
        )


def check_kind(raw: Mapping[str, object]) -> str:
    """Return the frame's kind once it is known to be a registered kind string.

    Args:
        raw: The dict parse_frame returned.

    Returns:
        The kind string, registered.

    Raises:
        MalformedFrameError: ``kind`` is not a string.
        UnknownKindError: ``kind`` is a string but not registered.
    """
    kind = raw["kind"]
    if not isinstance(kind, str):
        raise MalformedFrameError(f"The frame's kind {kind!r} is not a string.")
    # spec_for raises UnknownKindError with the kind named; that is exactly the codec's message.
    spec_for(kind)
    return kind


def build_envelope(kind: str, raw: Mapping[str, object]) -> Envelope:
    """Validate the payload with the registered class, then the envelope around it.

    Args:
        kind: The registered kind check_kind returned.
        raw: The dict parse_frame returned, signature included.

    Returns:
        The Envelope the frame carried, with a typed payload and its signature field set to
        whatever the frame carried.

    Raises:
        InvalidPayloadError: The payload or the envelope's own rules fail validation.
    """
    # The payload is validated with its registered class first because Envelope.payload is
    # typed as the base WaggleMessage, which would accept nothing; the typed instance then
    # replaces the raw dict for the envelope's own validation. Both failures are one error class:
    # the frame had a readable id, so the receiver answers with a control.error (spec section 7).
    try:
        payload = model_for(kind).model_validate(raw["payload"])
        return Envelope.model_validate({**raw, "payload": payload})
    except ValidationError as exc:
        # The raw ids ride on the error so the receiver can build the control.error reply (spec
        # section 7) without parsing the sentence; a value that is not text is left out.
        raise InvalidPayloadError(
            f"The frame with id {raw['id']!r} of kind {kind!r} failed validation: "
            f"{_describe(exc)}.",
            message_id=_text_or_none(raw["id"]),
            sender=_text_or_none(raw["sender"]),
            kind=kind,
            correlation_id=_text_or_none(raw["correlation_id"]),
        ) from exc


def _reject_non_finite(constant: str) -> object:
    """Refuse NaN and Infinity, which Python's json accepts but JSON (RFC 8259) does not have."""
    raise ValueError(f"The JSON constant {constant} is not part of JSON.")


def _text_or_none(value: object) -> str | None:
    """Return ``value`` when it is a string, else None: a raw wire id is only usable as text."""
    return value if isinstance(value, str) else None


def _describe(exc: ValidationError) -> str:
    """Summarise a ValidationError by field location and message, never by input value."""
    # pydantic's own str(exc) prints each input value, which on a Real Cell may be C2 content;
    # locations and messages are enough to debug and safe to log.
    return "; ".join(
        f"{'.'.join(str(part) for part in error['loc']) or '<root>'}: {error['msg']}"
        for error in exc.errors()
    )
