"""Generate the Landing Board's OpenAPI document from the route table, deterministically.

The Landing Board (the Hive Entrance's versioned API) is a published contract (ADR-0034): programs
are written from ``docs/entrance/openapi.json`` alone, so the document is generated, never
hand-written, from the same route table both listeners are built from, and committed; a test fails
when the generated document differs from the committed one, so every route change is a visible
contract diff. FastAPI's generator describes every row (loopback-only ones too, so a client knows
they exist) with ``x-hive-capability``, ``x-hive-listeners``, ``x-hive-c2`` and ``x-hive-effect``;
this module adds the ``HiveSession`` security scheme, the ``x-hive-signing`` extension (every
signed string, its fields and encodings, with a worked example built by the very functions the
Entrance verifies with) and ``x-hive-streams`` (the WebSocket views, their first frame and their
frames). The rendering is stable: sorted keys, two-space indent, a trailing newline.

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
    - docs/adr/0034-landing-board-versioning-and-push.md for the document's role.
    - docs/adr/0033-landing-board-enrolment-two-factor-login-and-exposure.md for the signing rules.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path

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
from hivemind.entrance.gate import RouteTable

DOCUMENT_PATH = Path(
    "docs/entrance/openapi.json"
)  # Where the document is committed, from the root.
_REF_TEMPLATE = "#/components/schemas/{model}"  # Where every schema of the document lives.
# A worked example of the request string, built by the function the Entrance verifies with.
_EXAMPLE_TIMESTAMP = 1_767_225_600  # 2026-01-01T00:00:00Z.
_EXAMPLE_NONCE = "AAECAwQFBgcICQoLDA0ODw"  # Sixteen bytes 0x00..0x0f, unpadded base64url.
_EXAMPLE_BODY = b'{"text":"tidy the garden"}'  # A goal submission's body.
_DOCUMENT = TypeAdapter(dict[str, JsonValue])  # FastAPI's dict, checked into plain JSON values.

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
    document["x-hive-signing"] = _signing()
    document["x-hive-streams"] = _streams(active, _object(components.setdefault("schemas", {})))
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
    }


def _streams(table: RouteTable, schemas: dict[str, JsonValue]) -> list[JsonValue]:
    """Describe every WebSocket view, adding its frame schemas to the document's components."""
    hello = _add_schema(SocketHello, schemas)
    described: list[JsonValue] = []
    for view in table.sockets:
        described.append(
            {
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
        )
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
