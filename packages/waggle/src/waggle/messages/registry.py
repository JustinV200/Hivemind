"""Map every Waggle kind string to its message class, shape and reply kind: the registry.

Waggle is the Hive's bee-to-bee wire protocol (named after the honeybee waggle dance). Every
frame carries a ``kind`` string of the form ``<family>.<snake_name>`` that tells the receiver
which pydantic model to validate the payload with. The list of kinds lives in exactly one file,
``waggle.messages.catalogue``, as plain rows; this module gives each row its typed form, a
``MessageSpec`` fixing the kind's class, its ``MessageShape`` (request, reply or event, which
decides what the envelope's ``correlation_id`` must hold) and the kind it replies to or usually
follows from, checks the rows once at import (the kind pattern, no duplicate kind or class, no
reply to an unregistered kind) and builds the lookups the codec and the envelope need
(``spec_for``, ``model_for``, ``kind_for``), which raise ``UnknownKindError`` on a miss. A
message class never carries its own kind, so renaming a kind, changing its shape or adding a
family is a change to the catalogue and nothing else (plus the spec, which the drift test keeps
in step).

Fits into the Hive:
    Its own layer (used by every layer in hivemind and by pollen, the lightweight device
    connector), inside the waggle package. Called by waggle.envelope (to check that kind and
    payload agree and to apply the shape rule) and waggle.codec (to pick the payload model for
    a decoded frame); imports the catalogue and waggle.messages.base, and no family module
    ever imports it back.

Key invariants:
    - MESSAGE_SPECS is the catalogue row for row and in its order, so all_kinds() follows the
      spec's catalogue order; the catalogue is the only kind list in the code and the spec's
      table the only one in prose, and tests/test_spec_drift.py fails until the two list the
      same sixty-six kinds with the same classes, shapes and replies.
    - Every kind and every model class appears in MESSAGE_SPECS exactly once, and every
      replies_to names a registered kind; the module refuses to import otherwise.
    - The lookups are read-only once built; nothing registers a kind behind the tuple's back.

See Also:
    - docs/waggle/spec.md section 3 (shapes) and section 8 (the catalogue table).
    - waggle.messages.catalogue for the rows this module indexes.
    - waggle.messages.base for MessageShape and WaggleMessage.
    - waggle.envelope and waggle.codec for the two callers.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from waggle.errors import UnknownKindError
from waggle.messages.base import KIND_PATTERN, MessageShape, WaggleMessage
from waggle.messages.catalogue import CATALOGUE

__all__ = ["MESSAGE_SPECS", "MessageSpec", "all_kinds", "kind_for", "model_for", "spec_for"]

_KIND_RE = re.compile(KIND_PATTERN)  # Compiled once: every spec is checked against it at import.


@dataclass(frozen=True, slots=True)
class MessageSpec:
    """Everything the protocol fixes about one kind: its class, its shape and its reply kind."""

    kind: str  # <family>.<snake_name>, the string on the wire.
    model: type[WaggleMessage]  # The payload class a frame of this kind validates with.
    shape: MessageShape  # Decides what the envelope's correlation_id must hold (spec section 3).
    replies_to: str | None  # The request a reply answers, or the antecedent an event follows.


# The complete kind list in its typed form: one MessageSpec per catalogue row, in the catalogue's
# (the spec table's) order. The rows themselves live in waggle.messages.catalogue and nowhere
# else; this tuple exists so callers and tests handle specs, never bare tuples.
MESSAGE_SPECS: tuple[MessageSpec, ...] = tuple(MessageSpec(*row) for row in CATALOGUE)


def spec_for(kind: str) -> MessageSpec:
    """Return the MessageSpec registered for ``kind``.

    Args:
        kind: A kind string as it appears on the wire, e.g. ``"control.ping"``.

    Returns:
        The registered spec: class, shape and reply kind.

    Raises:
        UnknownKindError: ``kind`` is not registered.
    """
    spec = _SPEC_BY_KIND.get(kind)
    # A miss is the codec's "peer speaks a kind this node has no model for" case, so it raises
    # the protocol error whose code (waggle.codec.unknown_kind) a transport reports.
    if spec is None:
        raise UnknownKindError(f"Kind {kind!r} is not registered in the Waggle message registry.")
    return spec


def model_for(kind: str) -> type[WaggleMessage]:
    """Return the payload class a frame of ``kind`` validates with.

    Args:
        kind: A kind string as it appears on the wire.

    Returns:
        The WaggleMessage subclass registered for ``kind``.

    Raises:
        UnknownKindError: ``kind`` is not registered.
    """
    return spec_for(kind).model


def kind_for(model_type: type[WaggleMessage]) -> str:
    """Return the kind string registered for a payload class.

    Args:
        model_type: The exact class of a payload (``type(payload)``); a subclass of a registered
            class is not itself registered.

    Returns:
        The kind string, e.g. ``"control.ping"`` for ``Ping``.

    Raises:
        UnknownKindError: ``model_type`` is not registered.
    """
    kind = _KIND_BY_MODEL.get(model_type)
    # Looked up by exact class, never by isinstance, so a stray subclass can never be sent
    # under its parent's kind and validated as something it is not on the other side.
    if kind is None:
        raise UnknownKindError(
            f"Message class {model_type.__name__} is not registered in the Waggle message registry."
        )
    return kind


def all_kinds() -> tuple[str, ...]:
    """Return every registered kind string, in registration (catalogue) order.

    Returns:
        A tuple of kind strings; the drift test compares it to the spec's catalogue table.
    """
    return tuple(spec.kind for spec in MESSAGE_SPECS)


def _index(specs: tuple[MessageSpec, ...]) -> tuple[Mapping[str, MessageSpec], Mapping[type, str]]:
    """Build the two lookups from ``specs``, refusing duplicates and dangling replies."""
    by_kind: dict[str, MessageSpec] = {}
    by_model: dict[type, str] = {}
    # Each spec must bring a new kind and a new class: a duplicate of either would make one of
    # the two lookups silently pick a winner, so the module refuses to import instead.
    for spec in specs:
        if _KIND_RE.fullmatch(spec.kind) is None:
            raise ValueError(f"Kind {spec.kind!r} does not match the <family>.<snake_name> shape.")
        if spec.kind in by_kind:
            raise ValueError(f"Kind {spec.kind!r} is registered more than once.")
        if spec.model in by_model:
            raise ValueError(
                f"Message class {spec.model.__name__} is registered more than once "
                f"({by_model[spec.model]!r} and {spec.kind!r})."
            )
        by_kind[spec.kind] = spec
        by_model[spec.model] = spec.kind
    # replies_to is checked after the pass so a reply may name a request registered later in
    # the tuple; a reply to a kind that does not exist can never be correlated by tooling.
    for spec in specs:
        if spec.replies_to is not None and spec.replies_to not in by_kind:
            raise ValueError(
                f"Kind {spec.kind!r} replies to {spec.replies_to!r}, which is not registered."
            )
    return MappingProxyType(by_kind), MappingProxyType(by_model)


# Built once at import; read-only proxies so nothing can register a kind behind the tuple's back.
_SPEC_BY_KIND, _KIND_BY_MODEL = _index(MESSAGE_SPECS)
