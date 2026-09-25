"""Generate the Landing Board's OpenAPI document from the route table, deterministically.

The Landing Board (the Hive Entrance's versioned API) is a published contract (ADR-0042): programs
are written from ``docs/entrance/openapi.json`` alone, so the document is generated, never
hand-written, from the same route table both listeners are built from, and committed; a test fails
when the generated document differs from the committed one, so every route change is a visible
contract diff. FastAPI's generator describes every row (loopback-only ones too, so a client knows
they exist) with ``x-hive-capability`` (absent where a session alone, or nothing, is needed),
``x-hive-listeners``, ``x-hive-c2`` and ``x-hive-effect`` (and a raw-body row, the voice clip, with
``x-hive-body``); this module adds the ``HiveSession`` security scheme, the ``x-hive-signing``
extension (every signed string, its fields and encodings, with worked examples built by the very
functions the Entrance verifies with), ``x-hive-streams`` (the WebSocket views, their first frame,
their frames, and for the chat view the push-to-talk frames a client sends and the frames that
answer them) and what a client must know that no schema says: ``x-hive-versioning`` (ADR-0042's
additive rule, read from the client's side), ``x-hive-socket-close`` (every close code a view sends)
and ``x-hive-bare-refusals`` (the refusals answered before routing, which carry no body). Every
object a client reads (a response body or a stream frame) is published open, because a later
``/v1/`` may add a field to it; a request-only object stays closed, as the Entrance refuses a field
it does not know; and each enum whose vocabulary may grow within ``/v1/`` is marked ``x-hive-open``.
The rendering is stable: sorted keys, two-space indent, a trailing newline.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance``. Called by the
    Entrance runtime (the document each listener serves), the drift test and
    ``scripts/write_landing_board.py``. Calls into ``hivemind.entrance.app`` and
    ``hivemind.entrance.auth.canonical``.

Key invariants:
    - The same table always renders the same bytes.
    - No manifest value (a port, a name, a threshold) enters the document: it is the same for every
      Hive.

See Also:
    - docs/adr/0042-landing-board-versioning-and-push.md for the document's role.
    - docs/adr/0041-landing-board-enrolment-two-factor-login-and-exposure.md for the signing rules.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from pathlib import Path
from types import MappingProxyType

from pydantic import BaseModel, JsonValue, TypeAdapter

from hivemind.entrance.app import SESSION_SCHEME, build_fastapi, route_table
from hivemind.entrance.auth.canonical import (
    ENROL_TAG,
    LOGIN_TAG,
    MIN_NONCE_BYTES,
    REQUEST_TAG,
    WEBHOOK_TAG,
    WEBSOCKET_TAG,
    request_string,
    sha256_hex,
)
from hivemind.entrance.auth.session import (
    SOCKET_HELLO_DEADLINE_S,
    Listener,
    SocketHello,
)
from hivemind.entrance.enrol import canonical_invite_code, invite_code_hash
from hivemind.entrance.gate import RouteTable
from hivemind.entrance.gate.middleware import BODY_READ_TIMEOUT_S, MAX_BODY_BYTES
from hivemind.entrance.streams import CloseReason

DOCUMENT_PATH = Path(
    "docs/entrance/openapi.json"
)  # Where the document is committed, from the root.
_REF_TEMPLATE = "#/components/schemas/{model}"  # Where every schema of the document lives.
# A worked example of the request string, built by the function the Entrance verifies with.
_EXAMPLE_TIMESTAMP = 1_767_225_600  # 2026-01-01T00:00:00Z.
_EXAMPLE_NONCE = "AAECAwQFBgcICQoLDA0ODw"  # Sixteen bytes 0x00..0x0f, unpadded base64url.
_EXAMPLE_BODY = b'{"text":"tidy the garden"}'  # A goal submission's body.
# An invite code as a person might retype it: the base32 of bytes 0x00..0x0f, lower case, spaced.
_EXAMPLE_TYPED_CODE = "aaaq eaye auda ocaj bifq ydio b4"
_DOCUMENT = TypeAdapter(dict[str, JsonValue])  # FastAPI's dict, checked into plain JSON values.
_SCHEMA_PREFIX = "#/components/schemas/"  # Where a reference into the components points.
# The enums whose vocabulary may grow within /v1/ (ADR-0042: a new member is additive only for an
# enum the document marks open). State machines and security tiers stay closed: a new state or
# tier changes what every client must do, so it waits for /v2/.
_OPEN_ENUMS: Mapping[str, str] = MappingProxyType(
    {
        "ActionKind": "Step-up covers new sensitive actions as they are built.",
        "ChannelKind": "New push channels join (the native Android channel, roadmap 12.12).",
        "ChatKind": "New kinds of chat line join the one conversation.",
        "GoalSource": "New ways a person gives the Hive a goal join.",
        "ModelSlot": "New model slots join as new roles need a model.",
        "NoticeKind": "New notices join as the Hive learns new things to wait on.",
        "OsFamily": "New device families join the Swarm (phase 11).",
        "PostconditionKind": "New acceptance checks join as new tools do.",
    }
)

__all__ = ["DOCUMENT_PATH", "openapi_document", "render_document", "write_document"]


def openapi_document(table: RouteTable | None = None) -> dict[str, JsonValue]:
    """Build the Landing Board's OpenAPI document from the route table.

    Args:
        table: The route table; the Entrance's own when None.

    Returns:
        The document, as JSON-compatible data.
    """
    active = table if table is not None else route_table()
    app = build_fastapi(active, active.routes, sockets=False)
    document = _DOCUMENT.validate_python(app.openapi())
    components = _object(document.setdefault("components", {}))
    components["securitySchemes"] = {SESSION_SCHEME: _session_scheme()}
    schemas = _object(components.setdefault("schemas", {}))
    document["x-hive-signing"] = _signing()
    document["x-hive-streams"] = _streams(active, schemas)
    # Last, once every schema a stream frame needs is in the components too.
    _open_what_is_read(document, schemas)
    document["x-hive-versioning"] = _versioning()
    document["x-hive-socket-close"] = _socket_close()
    document["x-hive-bare-refusals"] = _bare_refusals()
    return document


def render_document(table: RouteTable | None = None) -> bytes:
    """Render the document as the exact bytes committed: sorted keys, indent 2, final newline.

    Args:
        table: The route table; the Entrance's own when None.

    Returns:
        UTF-8 JSON.
    """
    text = json.dumps(openapi_document(table), sort_keys=True, indent=2, ensure_ascii=False)
    return (text + "\n").encode("utf-8")


def write_document(path: Path) -> bool:
    """Write the rendered document to ``path`` when it differs from what is there.

    Args:
        path: Where the document is committed (``docs/entrance/openapi.json`` from the root).

    Returns:
        True when the file was written (it was missing or out of date).
    """
    rendered = render_document()
    if path.exists() and path.read_bytes() == rendered:
        return False
    path.write_bytes(rendered)
    return True


def _session_scheme() -> dict[str, JsonValue]:
    """Describe the session scheme: a bearer token on a request signed by its binding key."""
    return {
        "type": "http",
        "scheme": "bearer",
        "description": (
            "The token POST /v1/auth/login answered, as Authorization: Bearer <token>. A token "
            "alone is refused: every request also carries X-Hive-Timestamp, X-Hive-Nonce and "
            "X-Hive-Signature, the session's binding key's signature over hive-request-v1 "
            "(x-hive-signing). A session works only on the listener it was opened on."
        ),
    }


def _signing() -> dict[str, JsonValue]:
    """Describe every signed string, its encodings, and one worked example."""
    body_digest = sha256_hex(_EXAMPLE_BODY)
    example = request_string("POST", "/v1/goals", _EXAMPLE_TIMESTAMP, _EXAMPLE_NONCE, body_digest)
    return {
        "encoding": "Each string is its tag then its fields, one per line, joined by a single "
        "line feed (U+000A), encoded as UTF-8; no field may contain a line break.",
        "timestamp": "Integer Unix seconds, in decimal.",
        "digest": "Lowercase hex SHA-256.",
        "nonce": f"Unpadded base64url over at least {MIN_NONCE_BYTES} random bytes; single use.",
        "signature": "Unpadded base64url: Ed25519's raw 64 bytes, or a WebCrypto P-256 "
        "signature in IEEE P1363 form (r || s, 64 bytes).",
        "headers": {
            "authorization": "Authorization: Bearer <token>",
            "timestamp": "X-Hive-Timestamp",
            "nonce": "X-Hive-Nonce",
            "signature": "X-Hive-Signature",
        },
        "strings": {
            ENROL_TAG: ["hive id", "invite code SHA-256 (hex)", "Ed25519 public key (hex)"],
            LOGIN_TAG: ["hive id", "device id", "challenge nonce"],
            REQUEST_TAG: [
                "method, upper case",
                "raw path and query exactly as sent (path?query, or path alone)",
                "X-Hive-Timestamp",
                "X-Hive-Nonce",
                "request body SHA-256 (hex; of the empty string when there is no body)",
            ],
            WEBSOCKET_TAG: ["raw path and query exactly as requested", "timestamp", "nonce"],
            WEBHOOK_TAG: ["subscription id", "event id", "X-Hive-Timestamp", "body SHA-256 (hex)"],
        },
        "example": {
            "method": "POST",
            "target": "/v1/goals",
            "timestamp": _EXAMPLE_TIMESTAMP,
            "nonce": _EXAMPLE_NONCE,
            "body": _EXAMPLE_BODY.decode("utf-8"),
            "signed_string": example.decode("utf-8"),
        },
        "webhooks": "A webhook carries X-Hive-Signature (the Hive's Ed25519 key over "
        "hive-webhook-v1), X-Hive-Timestamp and X-Hive-Event-Id (dedupe on it); verify with the "
        "key GET /v1/push/hive-key answers, which enrolment also returned.",
        "invite_code": _invite_code(),
    }


def _invite_code() -> dict[str, JsonValue]:
    """Say how an invite code is hashed for hive-enrol-v1, with a worked example."""
    canonical = canonical_invite_code(_EXAMPLE_TYPED_CODE)
    return {
        "form": "Hash the code in its canonical form, as UTF-8: its 26 base32 characters "
        "(A-Z, 2-7) upper case, in groups of four joined by '-' (the last group has two). A "
        "person may type it in any case, with or without dashes or spaces.",
        "example": {
            "typed": _EXAMPLE_TYPED_CODE,
            "canonical": canonical,
            "sha256": invite_code_hash(_EXAMPLE_TYPED_CODE),
        },
    }


def _versioning() -> dict[str, JsonValue]:
    """State ADR-0042's additive rule as a client must read it."""
    return {
        "additive": "Within /v1/ only additive changes ship: a new route, a new optional request "
        "field, a new response field, a new member of an enum marked x-hive-open. Anything else "
        "ships as /v2/, with /v1/ kept for one Brood release beside it.",
        "responses": "Every object a client reads (a response body or a stream frame) may gain "
        "fields within /v1/: ignore the ones you do not know.",
        "requests": "A request body with a field this document does not declare is refused "
        "(422): send only declared fields.",
        "enums": "An enum marked x-hive-open may gain members within /v1/: treat a member you do "
        "not know as unknown, never as an error. Every other enum is closed until /v2/.",
    }


def _socket_close() -> list[JsonValue]:
    """List every close code a live view sends, and what each means for the client."""
    closes: list[JsonValue] = [
        {"code": reason.code, "reason": reason.text, "name": reason.name.lower()}
        for reason in CloseReason
    ]
    # Refused before the upgrade is accepted, so it arrives as the handshake's own close.
    closes.append(
        {
            "code": 1008,
            "reason": "",
            "name": "refused_before_accept",
            "when": "The loopback listener refused the handshake's Host or a forwarding header, "
            "or the address is over its rate limit.",
        }
    )
    return closes


def _bare_refusals() -> list[JsonValue]:
    """List the refusals answered before routing: they carry no body and no ErrorBody."""
    return [
        {
            "status": 403,
            "listeners": ["loopback"],
            "when": "The Host is not a loopback name on this listener's port, or the request "
            "carries Forwarded, Via, X-Real-IP, X-Forwarded-* or Tailscale-*.",
        },
        {
            "status": 429,
            "listeners": ["loopback", "remote"],
            "when": "The peer address is over its request rate (a Hive setting); retry later.",
        },
        {
            "status": 413,
            "listeners": ["loopback", "remote"],
            "when": f"The request body is larger than {MAX_BODY_BYTES} bytes, or than the "
            "operation's own x-hive-body.max_bytes when it declares one.",
        },
        {
            "status": 408,
            "listeners": ["loopback", "remote"],
            "when": f"The request body did not arrive within {BODY_READ_TIMEOUT_S:g} seconds, or "
            "the operation's own x-hive-body.read_timeout_s when it declares one.",
        },
    ]


def _open_what_is_read(document: dict[str, JsonValue], schemas: dict[str, JsonValue]) -> None:
    """Open every object a client reads, and mark each enum that may grow (ADR-0042).

    FastAPI publishes every model closed (``additionalProperties: false``), which a client that
    validates what it reads would take as a promise that no field is ever added. Only a request
    object is truly closed: the Entrance refuses a field it does not know.
    """
    roots: set[str] = set()
    paths = _object(document.get("paths", {}))
    for operations in paths.values():
        for operation in _object(operations).values():
            _references(_object(operation).get("responses", {}), roots)
    # A stream frame is read too, and so is a reply to a client's frame; the first frame and the
    # client's own frames are sent, like a request body.
    streams = document.get("x-hive-streams", [])
    for view in streams if isinstance(streams, list) else []:
        _references(_object(view).get("frame", {}), roots)
        _references(_object(view).get("reply_frames", []), roots)
    for name in _closure(roots, schemas):
        schema = _object(schemas[name])
        if schema.get("additionalProperties") is False:
            del schema["additionalProperties"]
    for name in _OPEN_ENUMS:
        if name in schemas:
            _object(schemas[name])["x-hive-open"] = True


def _closure(roots: set[str], schemas: dict[str, JsonValue]) -> set[str]:
    """Every schema ``roots`` reach through references, the roots included."""
    reached: set[str] = set()
    pending = list(roots)
    while pending:
        name = pending.pop()
        if name in reached or name not in schemas:
            continue
        reached.add(name)
        found: set[str] = set()
        _references(schemas[name], found)
        pending.extend(found - reached)
    return reached


def _references(node: JsonValue, found: set[str]) -> None:
    """Collect the component names every ``$ref`` under ``node`` points at."""
    if isinstance(node, dict):
        reference = node.get("$ref")
        if isinstance(reference, str) and reference.startswith(_SCHEMA_PREFIX):
            found.add(reference.removeprefix(_SCHEMA_PREFIX))
        for value in node.values():
            _references(value, found)
    elif isinstance(node, list):
        for value in node:
            _references(value, found)


def _streams(table: RouteTable, schemas: dict[str, JsonValue]) -> list[JsonValue]:
    """Describe every WebSocket view, adding its frame schemas to the document's components."""
    hello = _add_schema(SocketHello, schemas)
    described: list[JsonValue] = []
    for view in table.sockets:
        entry: dict[str, JsonValue] = {
            "path": view.path,
            "summary": view.summary,
            "x-hive-capability": view.access.capability,
            "x-hive-c2": view.access.c2,
            "x-hive-listeners": _listener_names(view.listeners),
            "first_frame": {
                "schema": hello,
                "deadline_s": SOCKET_HELLO_DEADLINE_S,
                "signs": WEBSOCKET_TAG,
            },
            "frame": _add_schema(view.frame_model, schemas),
        }
        # A view the client talks to (push-to-talk on the chat): what it sends, what answers it.
        if view.client_frames:
            entry["client_frames"] = [_add_schema(model, schemas) for model in view.client_frames]
            entry["reply_frames"] = [_add_schema(model, schemas) for model in view.reply_frames]
        described.append(entry)
    return described


def _listener_names(listeners: Iterable[Listener]) -> list[JsonValue]:
    """Return the listeners' names, sorted, as JSON values."""
    # A list of JSON values, not of strings: a JSON array is invariant in its element type.
    names: list[JsonValue] = []
    names.extend(sorted(listener.value for listener in listeners))
    return names


def _add_schema(model: type[BaseModel], schemas: dict[str, JsonValue]) -> dict[str, JsonValue]:
    """Put ``model``'s schema (and those it refers to) in the components; return a reference."""
    schema = model.model_json_schema(ref_template=_REF_TEMPLATE, mode="serialization")
    definitions = _object(schema.pop("$defs", {}))
    # A schema the routes already published stays as FastAPI generated it.
    for name, definition in definitions.items():
        schemas.setdefault(name, definition)
    schemas.setdefault(model.__name__, schema)
    return {"$ref": _REF_TEMPLATE.format(model=model.__name__)}


def _object(value: JsonValue) -> dict[str, JsonValue]:
    """Narrow a JSON value that must be an object."""
    if not isinstance(value, dict):
        raise TypeError("The OpenAPI document holds an object here.")
    return value
