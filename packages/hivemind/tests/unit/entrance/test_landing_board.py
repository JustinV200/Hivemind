"""Test hivemind.entrance.landing_board: the committed contract, generated and never drifting.

Fits into the Hive:
    Test infrastructure (codingrules section 14.2), not shipped.

Key invariants:
    - None: this module holds tests only.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from hivemind.entrance.app import SESSION_SCHEME, route_table
from hivemind.entrance.auth import request_string, sha256_hex
from hivemind.entrance.auth.session import SOCKET_HELLO_DEADLINE_S
from hivemind.entrance.enrol import canonical_invite_code, invite_code_hash
from hivemind.entrance.gate import LOOPBACK_ONLY
from hivemind.entrance.landing_board import DOCUMENT_PATH, openapi_document, render_document
from hivemind.entrance.streams import CloseReason

REPOSITORY_ROOT = Path(__file__).resolve().parents[5]  # tests/unit/entrance -> the checkout.


def _document() -> Any:  # noqa: ANN401 -- a parsed JSON document, read field by field.
    """The rendered document, parsed back: plain dicts and lists a test indexes freely."""
    return json.loads(render_document())


def test_the_committed_document_is_exactly_what_the_route_table_renders() -> None:
    committed = (REPOSITORY_ROOT / DOCUMENT_PATH).read_bytes()

    assert committed == render_document(), (
        "docs/entrance/openapi.json drifted: run scripts/write_landing_board.py and commit it."
    )


def test_rendering_is_deterministic_sorted_and_ends_with_a_newline() -> None:
    first, second = render_document(), render_document()

    assert first == second
    assert first.endswith(b"}\n")
    assert json.loads(first) == json.loads(json.dumps(json.loads(first), sort_keys=True))


def test_every_operation_declares_its_capability_listeners_and_security() -> None:
    paths = _document()["paths"]

    for route in route_table().routes:
        operation = paths[route.path][route.method.lower()]
        # FastAPI drops a null extension: an absent capability means none is needed.
        assert operation.get("x-hive-capability") == route.access.capability
        assert operation["x-hive-listeners"] == sorted(item.value for item in route.listeners)
        expected: list[object] = [{SESSION_SCHEME: []}] if route.access.authenticated else []
        assert operation["security"] == expected
        if route.listeners == LOOPBACK_ONLY:
            assert operation["x-hive-listeners"] == ["loopback"]


def test_the_session_scheme_and_signing_rules_are_published() -> None:
    document = _document()

    schemes = document["components"]["securitySchemes"]
    example = document["x-hive-signing"]["example"]

    assert list(schemes) == [SESSION_SCHEME]
    assert schemes[SESSION_SCHEME]["scheme"] == "bearer"
    body = str(example["body"]).encode("utf-8")
    expected = request_string(
        "POST", "/v1/goals", int(example["timestamp"]), str(example["nonce"]), sha256_hex(body)
    )
    assert example["signed_string"] == expected.decode("utf-8")


def test_every_stream_names_its_first_frame_and_deadline() -> None:
    streams = _document()["x-hive-streams"]

    paths = {stream["path"] for stream in streams}

    assert paths == {view.path for view in route_table().sockets}
    for stream in streams:
        assert stream["first_frame"]["deadline_s"] == SOCKET_HELLO_DEADLINE_S


def test_the_rendered_bytes_parse_back_to_the_document() -> None:
    assert _document() == json.loads(json.dumps(openapi_document()))


def _read_schemas(document: Any) -> set[str]:  # noqa: ANN401 -- the parsed document, as above.
    """Every component a response or a stream frame reaches: what a client reads."""
    schemas = document["components"]["schemas"]
    reached: set[str] = set()
    pending = [
        text.rsplit("/", 1)[1]
        for text in _refs(
            [op["responses"] for ops in document["paths"].values() for op in ops.values()]
        )
        + _refs([view["frame"] for view in document["x-hive-streams"]])
    ]
    while pending:
        name = pending.pop()
        if name not in reached:
            reached.add(name)
            pending.extend(text.rsplit("/", 1)[1] for text in _refs(schemas[name]))
    return reached


def _refs(node: Any) -> list[str]:  # noqa: ANN401 -- any JSON value.
    """Every $ref string under ``node``."""
    if isinstance(node, dict):
        own = [node["$ref"]] if isinstance(node.get("$ref"), str) else []
        return own + [ref for value in node.values() for ref in _refs(value)]
    if isinstance(node, list):
        return [ref for value in node for ref in _refs(value)]
    return []


def test_every_object_a_client_reads_is_open_and_only_requests_are_closed() -> None:
    document = _document()
    schemas = document["components"]["schemas"]
    read = _read_schemas(document)

    # ADR-0034: a later /v1 may add a response field, so nothing read may promise there is none.
    assert all(schemas[name].get("additionalProperties") is not False for name in read)
    closed = {
        name for name, schema in schemas.items() if schema.get("additionalProperties") is False
    }
    assert closed and closed.isdisjoint(read)
    assert {"GoalSubmission", "LoginRequest", "SocketHello"} <= closed


def test_the_enums_that_may_grow_are_marked_open_and_state_machines_are_not() -> None:
    schemas = _document()["components"]["schemas"]

    assert schemas["NoticeKind"]["x-hive-open"] is True
    assert schemas["ChannelKind"]["x-hive-open"] is True
    for closed in ("TaskStatus", "DeviceStatus", "HoneyClearance", "CombShieldLevel"):
        assert "x-hive-open" not in schemas[closed]
    assert "enums" in _document()["x-hive-versioning"]


def test_the_invite_code_example_hashes_as_the_entrance_verifies() -> None:
    example = _document()["x-hive-signing"]["invite_code"]["example"]

    assert example["canonical"] == canonical_invite_code(example["typed"])
    assert example["sha256"] == invite_code_hash(example["canonical"])
    assert example["sha256"] == sha256_hex(example["canonical"].encode("utf-8"))


def test_every_close_code_and_bare_refusal_is_published() -> None:
    document = _document()
    codes = {close["code"] for close in document["x-hive-socket-close"]}

    assert {reason.code for reason in CloseReason} <= codes
    assert {refusal["status"] for refusal in document["x-hive-bare-refusals"]} == {
        403,
        408,
        413,
        429,
    }
