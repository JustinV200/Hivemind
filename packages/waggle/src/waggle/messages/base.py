"""Define what every Waggle message family builds on: payload root, shapes, bounds, field aliases.

Waggle is the Hive's bee-to-bee wire protocol (named after the honeybee waggle dance). Every
payload that travels inside an Envelope (the outer wrapper that carries the id, the addresses, the
kind, the time and the signature) subclasses ``WaggleMessage``, the frozen, extras-forbidding
pydantic root defined here, so one model configuration governs how every family serialises and
validates. ``MessageShape`` names the three ways a message relates to another (request, reply,
event); the registry records one per kind and the envelope enforces its correlation rule. The
constants are the bounds the spec's catalogue conventions share across families, named once so
every family file pins the same number. The typed field aliases are how a family declares an id
or a timestamp field without writing a validator of its own: ``UtcDatetime`` rejects a naive
datetime and normalises an aware one to UTC, and one ``<Kind>IdField`` per ``waggle.ids.IdKind``
validates the field with ``waggle.ids.parse_id`` through ``check_id`` (its pydantic-friendly form,
because pydantic only turns a ValueError into a validation error and ``InvalidIdError`` is not
one); ``id_validator`` builds the same check for a field that may hold several kinds.

Fits into the Hive:
    Its own layer (used by every layer in hivemind and by pollen, the lightweight device
    connector), inside the waggle package. Subclassed by every family module under
    waggle.messages, which also declare their fields with the aliases here; read by
    waggle.messages.registry (MessageShape) and waggle.envelope (WaggleMessage, UtcDatetime,
    the id aliases); calls into waggle.ids only.

Key invariants:
    - Every WaggleMessage subclass is frozen and rejects unknown keys, because it inherits the
      model_config defined here and never overrides it.
    - A message class never carries its own kind string: the registry is the only place kinds
      live (roadmap 1.3), so this module knows nothing about kinds beyond KIND_PATTERN's shape.
    - VALUE_MODEL_CONFIG is the very object WaggleMessage.model_config is, so a value model (a
      small structure that rides on messages of more than one family but is not itself a kind)
      behaves on the wire exactly like a message without being registrable as one.
    - Exactly one ``<Kind>IdField`` alias exists per IdKind member, and every id that passes one
      also passes waggle.ids.parse_id for that kind (tests/messages/test_base.py checks both).
    - A datetime that passes UtcDatetime is timezone-aware with a zero offset.

See Also:
    - docs/waggle/spec.md section 2 (Time) and section 8 (catalogue conventions) for the rules
      the aliases and the constants pin.
    - waggle.messages.registry for the kind list and the MessageShape fixed per kind.
    - waggle.envelope for the Envelope that carries a WaggleMessage and enforces the shape rule.
    - waggle.ids for IdKind, the NewTypes and parse_id, which check_id wraps.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from functools import partial
from typing import Annotated

from pydantic import AfterValidator, BaseModel, ConfigDict

from waggle.errors import InvalidIdError
from waggle.ids import (
    AlarmId,
    CellId,
    DeviceId,
    EventId,
    GrantId,
    HiveId,
    IdKind,
    LeaseId,
    MessageId,
    NodeId,
    TaskId,
    ToolId,
    WardenId,
    WorkerId,
    parse_id,
)

MAX_REASON_CHARS = 1_000  # A reason is a sentence or two for the Pheromone Trail, never an essay.
MAX_CHUNK_BYTES = 262_144  # 256 KiB: base64 inflates it by a third and it still fits a 1 MiB frame.
MAX_PATH_CHARS = 4_096  # PATH_MAX on Linux; no path on the wire is longer than the OS allows.
MAX_SLOT_CHARS = 32  # A model slot is a short UPPER_SNAKE role name (PLANNER, JUDGE, ...).
SLOT_PATTERN = r"^[A-Z][A-Z_]*$"  # UPPER_SNAKE, letter first: the shape of a ModelSlot member name.
SHA256_PATTERN = r"^[0-9a-f]{64}$"  # Lowercase hex of a 32-byte digest: the one digest form.
KIND_PATTERN = r"^[a-z_]+\.[a-z_]+$"  # <family>.<snake_name>: the shape of a kind, not the list.
MAX_SUB_BEES_ON_WIRE = 64  # Every count or list of sub-bees, so a cap and a heartbeat agree.
MAX_MESSAGE_AGE_S = 604_800  # Seven days: how long seen ids are kept; an older sent_at is dropped.
MAX_CLOCK_SKEW_S = 300  # Five minutes: the most a sent_at may run ahead of the receiver's clock.
MAX_OPEN_CHUNK_GROUPS = 8  # Incomplete chunk groups a receiver holds per sender before refusing.
CHUNK_GROUP_TIMEOUT_S = 60.0  # An incomplete chunk group idle this long is discarded.
DEFAULT_MAX_OUTPUT_BYTES = 16_777_216  # 16 MiB: default cap on a session's output or file read.

__all__ = [
    "CHUNK_GROUP_TIMEOUT_S",
    "DEFAULT_MAX_OUTPUT_BYTES",
    "KIND_PATTERN",
    "MAX_CHUNK_BYTES",
    "MAX_CLOCK_SKEW_S",
    "MAX_MESSAGE_AGE_S",
    "MAX_OPEN_CHUNK_GROUPS",
    "MAX_PATH_CHARS",
    "MAX_REASON_CHARS",
    "MAX_SLOT_CHARS",
    "MAX_SUB_BEES_ON_WIRE",
    "SHA256_PATTERN",
    "SLOT_PATTERN",
    "VALUE_MODEL_CONFIG",
    "AlarmIdField",
    "CellIdField",
    "DeviceIdField",
    "EventIdField",
    "GrantIdField",
    "HiveIdField",
    "LeaseIdField",
    "MessageIdField",
    "MessageShape",
    "NodeIdField",
    "TaskIdField",
    "ToolIdField",
    "UtcDatetime",
    "WaggleMessage",
    "WardenIdField",
    "WorkerIdField",
    "check_id",
    "id_validator",
]


class MessageShape(Enum):
    """How a kind relates to other messages; decides what an envelope's correlation_id must hold.

    Spec section 3. The registry fixes exactly one shape per kind, and the envelope's model
    validator applies the correlation rule of the shape registered for the kind it carries.
    """

    REQUEST = "request"  # Expects exactly one answer; correlation_id must be None.
    REPLY = "reply"  # Answers one request; correlation_id must be set to that request's id.
    EVENT = "event"  # Tells the recipient something happened; correlation_id is optional.


class WaggleMessage(BaseModel):
    """Root of every payload an Envelope carries; every message family subclasses it.

    It declares no fields of its own: it exists so that one configuration governs every payload
    on the wire. Each entry is there for a reason: ``frozen`` because a message is an immutable
    value once built (codingrules 8.5) and a frozen model is hashable and safe to share between
    tasks; ``extra="forbid"`` because an unknown key from a peer is a protocol error (a newer
    minor the receiver does not speak, or tampering) and must be rejected, never silently
    dropped; ``ser_json_bytes="base64"`` because JSON has no bytes type, so chunk fields go out
    as base64 text; ``val_json_bytes="base64"`` so the same text comes back in as bytes. A
    subclass never overrides model_config, and never carries its kind string: the registry maps
    kinds to classes.
    """

    # Written out literally rather than through a shared name, because the pydantic mypy plugin
    # only reads frozen=True off a literal ConfigDict on the class body; VALUE_MODEL_CONFIG below
    # is an alias of this very object so the two can never drift apart.
    model_config = ConfigDict(
        frozen=True, extra="forbid", ser_json_bytes="base64", val_json_bytes="base64"
    )


# The one configuration every value model in waggle.messages reuses: a value model is a plain
# BaseModel (not a WaggleMessage, so it can never be registered as a kind) that must still be
# frozen, forbid extras and carry bytes as base64 exactly as a message does.
VALUE_MODEL_CONFIG: ConfigDict = WaggleMessage.model_config


def check_id(value: str, kind: IdKind) -> str:
    """Validate an id field of ``kind`` inside a pydantic validator and return it unchanged.

    Args:
        value: The candidate id string a field validator received.
        kind: The IdKind the field may hold.

    Returns:
        ``value``, unchanged, now known to be a well-formed id of ``kind``.

    Raises:
        ValueError: ``value`` is not a well-formed id of ``kind``; pydantic turns this into a
            ValidationError on the field, which the codec reports as an invalid payload.
    """
    # parse_id raises InvalidIdError, a WaggleError that pydantic would let escape from the
    # validator as-is; re-raising as ValueError keeps the failure inside pydantic's report so a
    # bad id on the wire is an InvalidPayloadError like every other bad field, not a stray
    # InvalidIdError out of Codec.decode.
    try:
        return parse_id(value, kind)
    except InvalidIdError as exc:
        raise ValueError(str(exc)) from exc


def id_validator(*kinds: IdKind) -> AfterValidator:
    """Build the validator an ``Annotated`` id alias carries: accept an id of any of ``kinds``.

    The single-kind aliases below are built with it, and a family whose field may hold several
    kinds declares its own alias the same way, for example
    ``Annotated[WorkerId | WardenId, id_validator(IdKind.WORKER, IdKind.WARDEN)]``, so no family
    ever writes an id validator by hand.

    Args:
        *kinds: The IdKind members the field may hold, at least one.

    Returns:
        A pydantic AfterValidator that returns the id unchanged when it is a well-formed id of
        one of ``kinds`` and raises ValueError (a validation error on the field) otherwise.

    Raises:
        ValueError: ``kinds`` is empty; raised when the alias is built, never at validation time.
    """
    # An alias that accepts nothing is a programming error, caught at import of the family file.
    if not kinds:
        raise ValueError("id_validator needs at least one IdKind to validate against.")
    # One kind keeps parse_id's precise message (wrong prefix, wrong length, bad alphabet); with
    # several, none of those is the right message, so the union check names the accepted set.
    if len(kinds) == 1:
        return AfterValidator(partial(check_id, kind=kinds[0]))
    return AfterValidator(partial(_check_id_of_kinds, kinds=kinds))


def _check_id_of_kinds(value: str, kinds: tuple[IdKind, ...]) -> str:
    """Return ``value`` when it is a well-formed id of any of ``kinds``, else raise ValueError."""
    # The prefix decides which kind's check can possibly pass, so at most one parse_id call is
    # made: a prefix outside the accepted set fails before any ULID work.
    for kind in kinds:
        if value.startswith(f"{kind.value}_"):
            return check_id(value, kind)
    accepted = ", ".join(f"{kind.value}_" for kind in kinds)
    raise ValueError(f"id {value!r} does not start with one of the accepted prefixes {accepted}.")


def _to_utc(value: datetime) -> datetime:
    """Reject a naive datetime and return an aware one normalised to UTC (spec section 2)."""
    # A naive datetime names no instant: two nodes would read it in two zones, so it is refused
    # rather than assumed to be UTC. utcoffset() is checked as well as tzinfo because a tzinfo
    # whose utcoffset() is None is naive by the datetime module's own definition.
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("A Waggle timestamp must be timezone-aware; a naive datetime is rejected.")
    # Normalising to UTC (rather than only requiring an offset) makes two equal instants compare
    # and serialise identically whatever zone the sender used; a signature is unaffected because
    # canonical bytes are computed over the raw wire dict, never over the validated model.
    return value.astimezone(UTC)


# ──────────────────────────────────────────────────────────────────────────────
# Typed field aliases
# ──────────────────────────────────────────────────────────────────────────────

# These are module-level assignments evaluated at import, so they must follow the helpers they
# are built from; that is why they sit below the private functions instead of above them.

# A timezone-aware datetime normalised to UTC: every `_at` field and Envelope.sent_at use it.
UtcDatetime = Annotated[datetime, AfterValidator(_to_utc)]

# One alias per IdKind, each typed with the kind's NewType so mypy catches a TaskId handed to a
# CellId field, and each validated with parse_id so a malformed id fails at the wire.
HiveIdField = Annotated[HiveId, id_validator(IdKind.HIVE)]
CellIdField = Annotated[CellId, id_validator(IdKind.CELL)]
LeaseIdField = Annotated[LeaseId, id_validator(IdKind.LEASE)]
TaskIdField = Annotated[TaskId, id_validator(IdKind.TASK)]
WorkerIdField = Annotated[WorkerId, id_validator(IdKind.WORKER)]
WardenIdField = Annotated[WardenId, id_validator(IdKind.WARDEN)]
AlarmIdField = Annotated[AlarmId, id_validator(IdKind.ALARM)]
GrantIdField = Annotated[GrantId, id_validator(IdKind.GRANT)]
ToolIdField = Annotated[ToolId, id_validator(IdKind.TOOL)]
NodeIdField = Annotated[NodeId, id_validator(IdKind.NODE)]
EventIdField = Annotated[EventId, id_validator(IdKind.EVENT)]
DeviceIdField = Annotated[DeviceId, id_validator(IdKind.DEVICE)]
MessageIdField = Annotated[MessageId, id_validator(IdKind.MESSAGE)]
