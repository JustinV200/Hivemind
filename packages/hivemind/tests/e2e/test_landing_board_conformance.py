"""End-to-end: a program written from the committed OpenAPI document alone uses the Landing Board.

Roadmap step 10.5c's conformance test. The Landing Board (the Hive Entrance's versioned API,
ADR-0034) promises that a third-party program written from ``docs/entrance/openapi.json`` alone
works. ``e2e.landing_client`` is that program: it imports no Hive code, finds every operation by
method and path in the committed document, signs from ``x-hive-signing`` alone, and accepts a
reply only when its status is declared and its body matches the declared schema. Against
``hive serve``'s own composition over real loopback sockets (``e2e.entrance_stand``: a real Queen,
Warden and Drone over a scripted provider, the operator at the Hive Stand), the client learns the
Hive's id, redeems an invite and is refused while pending, logs in once approved, subscribes to
the live push stream with a signed first frame, submits a goal (202), answers the question the
Queen routed to the human, sees it withdrawn and the goal completed by push, reads the goal back,
chats with the Queen and reads the chat on its stream, then logs out. Refusals are checked the same
way: a bad signature, a replayed nonce and a stale timestamp; and, on a remote listener in ``vpn``
mode on a test address, every loopback-only operation is a 404.

Fits into the Hive:
    Test infrastructure (codingrules section 14.2), not shipped.

Key invariants:
    - None: this module holds tests only.
"""

from __future__ import annotations

import ast
import asyncio
import sys
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
from pathlib import Path

import httpx
import pytest
from builders.entrance import PASSWORD
from builders.entrance.serving import RigOptions, serving
from e2e.entrance_stand import ANSWER, CHAT, GOAL, QUESTION, REPLY, Stand, build_served, standing
from e2e.landing_client import (
    DeviceKey,
    GenericClient,
    LandingBoard,
    Prepared,
    Request,
    Session,
    StreamFeed,
    sha256_hex,
)

from hivemind.cli.compose.entrance import ServedHive
from hivemind.manifest import HiveManifest

pytestmark = pytest.mark.e2e

DOCUMENT = Path(__file__).resolve().parents[4] / "docs" / "entrance" / "openapi.json"
CLIENT_PACKAGE = Path(__file__).resolve().parent / "landing_client"
# What the generic client may import: the standard library, three generic libraries, itself.
CLIENT_IMPORTS = frozenset({"httpx", "websockets", "cryptography", "e2e"}) | frozenset(
    sys.stdlib_module_names
)
STALE_S = 3600  # A timestamp an hour old: far outside any request_skew_s.


@dataclass(frozen=True, slots=True)
class _Device:
    """An enrolled device as the program knows it: its key, its id and the Hive it joined."""

    key: DeviceKey
    device_id: str
    hive_id: str


@asynccontextmanager
async def _client(base_url: str) -> AsyncIterator[GenericClient]:
    """A generic client on one listener; no proxy, since the Entrance is on this host."""
    async with httpx.AsyncClient(base_url=base_url, timeout=10.0, trust_env=False) as http:
        yield GenericClient(http, LandingBoard.load(DOCUMENT))


async def _enrol(client: GenericClient, stand: Stand | None, code: str) -> _Device:
    """Redeem ``code`` with a fresh Ed25519 key; while pending, the device cannot log in."""
    hive = await client.call(Request("GET", "/v1/enrol/hive"))
    hive_id = str(hive.data["hive_id"])
    key = DeviceKey.generate()
    proof = {
        "hive_id": hive_id,
        "code_sha256": sha256_hex(code.encode("utf-8")),
        "public_key_hex": key.public_key_hex,
    }
    body = {
        "code": code,
        "public_key_hex": key.public_key_hex,
        "signature": key.sign(client.rules.message("hive-enrol-v1", proof)),
        "description": {"name": "garden-bot", "platform": "Linux", "user_agent": "conformance"},
    }
    redeemed = await client.call(Request("POST", "/v1/enrol/ed25519", body=body))
    assert redeemed.status == 202
    device_id = str(redeemed.data["device_id"])
    # PENDING: the declared refusal, the same one every device that may not log in gets.
    asked = Request("POST", "/v1/auth/challenge", body={"device_id": device_id})
    pending = await client.call(asked)
    assert pending.status == 401
    assert pending.data["error"] == "hivemind.entrance.authentication_failed"
    if stand is not None:
        await stand.approve(device_id)
    return _Device(key, device_id, hive_id)


async def _login(client: GenericClient, device: _Device, password: str) -> Session:
    """Answer a login challenge with the device key's signature and the operator password."""
    asked = Request("POST", "/v1/auth/challenge", body={"device_id": device.device_id})
    challenge = await client.call(asked)
    assert challenge.status == 200 and challenge.data["passkey_options"] is None
    nonce = str(challenge.data["nonce"])
    values = {"hive_id": device.hive_id, "device_id": device.device_id, "challenge": nonce}
    body = {
        "device_id": device.device_id,
        "nonce": nonce,
        "signature": device.key.sign(client.rules.message("hive-login-v1", values)),
        "password": password,
    }
    opened = await client.call(Request("POST", "/v1/auth/login", body=body))
    assert opened.status == 201
    return Session(str(opened.data["token"]), device.device_id, device.key)


def test_a_program_written_from_the_document_submits_subscribes_answers_and_chats(
    tmp_path: Path,
) -> None:
    # build_served runs outside any event loop, like `hive serve` itself.
    manifest, served = build_served(tmp_path)

    asyncio.run(_conformance(manifest, served))


async def _conformance(manifest: HiveManifest, served: ServedHive) -> None:
    """The async body of the scenario above."""
    async with standing(manifest, served) as stand, _client(stand.loopback_url) as client:
        device = await _enrol(client, stand, await stand.invite())
        session = await _login(client, device, stand.password)
        me = await client.call(Request("GET", "/v1/devices/me"), session)
        request_id = await _submit_subscribe_answer(client, stand, session)
        await _chat(client, stand, session)
        goal = await client.call(
            Request("GET", "/v1/goals/{request_id}", params={"request_id": request_id}), session
        )
        logout = await client.call(Request("POST", "/v1/auth/logout"), session)
        after = await client.call(Request("GET", "/v1/devices/me"), session)

    assert me.data["status"] == "APPROVED" and me.data["key_kind"] == "ed25519"
    assert goal.data["state"] == "PLANNED" and goal.data["finished_at"] is not None
    assert (logout.status, after.status) == (204, 401)


@asynccontextmanager
async def _subscribed(
    client: GenericClient, stand: Stand, session: Session
) -> AsyncIterator[StreamFeed]:
    """Open the live push stream and wait until the Entrance has attached it."""
    async with client.stream("/v1/push/stream", session) as push:
        live = stand.entrance.services.push.live
        # Wait on the Entrance's own state: the first frame admitted, the socket attached.
        await stand.until(lambda: session.device_id in live.live_devices())
        yield push


async def _submit_subscribe_answer(client: GenericClient, stand: Stand, session: Session) -> str:
    """Subscribe to push, submit the goal, answer its question; return the goal request id."""
    async with _subscribed(client, stand, session) as push:
        goal = Request("POST", "/v1/goals", body={"text": GOAL})
        accepted = await client.call(goal, session)
        asked = await push.until("question_waiting")
        inbox = await client.call(Request("GET", "/v1/inbox"), session)
        questions = inbox.data["questions"]
        assert isinstance(questions, list) and len(questions) == 1
        question = questions[0]
        assert isinstance(question, dict) and question["id"] == asked["ref"]
        path = {"question_id": str(question["id"])}
        answer = Request(
            "POST", "/v1/inbox/questions/{question_id}/answer", path, body={"text": ANSWER}
        )
        answered = await client.call(answer, session)
        withdrawn = await push.until("withdrawn", str(asked["ref"]))
        completed = await push.until("goal_completed", str(accepted.data["id"]))

    assert accepted.status == 202 and accepted.data["state"] == "RECEIVED"
    assert (question["text"], question["options"]) == (QUESTION, [])
    assert answered.data["question_status"] == "ANSWERED"
    assert withdrawn["ref"] == asked["ref"] and completed["ref"] == accepted.data["id"]
    return str(accepted.data["id"])


async def _chat(client: GenericClient, stand: Stand, session: Session) -> None:
    """Say something to the Queen, hear by push that she replied, read the chat and its stream."""
    async with _subscribed(client, stand, session) as push:
        posted = await client.call(Request("POST", "/v1/chat", body={"text": CHAT}), session)
        replied = await push.until("reply_waiting")
    page = await client.call(Request("GET", "/v1/chat", query={"limit": 50}), session)
    lines = page.data["entries"]
    assert isinstance(lines, list)
    async with client.stream("/v1/chat/stream", session, {"after": "0"}) as feed:
        first = await feed.next()

    said = [
        (line["author"], line["kind"], line["text"]) for line in lines if isinstance(line, dict)
    ]
    assert ("queen", "question", QUESTION) in said
    assert said[-2:] == [("human", "message", CHAT), ("queen", "reply", REPLY)]
    assert posted.status == 202 and replied["ref"] == lines[-1]["id"]
    assert first["type"] == "chat" and first["entry"] == lines[0]


def test_a_bad_signature_a_replayed_nonce_and_a_stale_timestamp_are_refused_as_declared(
    tmp_path: Path,
) -> None:
    manifest, served = build_served(tmp_path)

    asyncio.run(_refusals(manifest, served))


async def _refusals(manifest: HiveManifest, served: ServedHive) -> None:
    """Send one good signed request, then the ways a signed request is refused."""
    async with standing(manifest, served) as stand, _client(stand.loopback_url) as client:
        device = await _enrol(client, stand, await stand.invite())
        session = await _login(client, device, stand.password)
        me = Request("GET", "/v1/devices/me")
        good = client.prepare(me, session)
        answers = {
            "good": await client.send(good),
            "replayed": await client.send(good),
            "bad signature": await client.send(_tampered(client.prepare(me, session))),
            "stale": await client.send(_stale(client, good, session)),
            "unsigned": await client.send(_unsigned(client.prepare(me, session))),
            "anonymous": await client.send(replace(good, headers={})),
        }

    statuses = {name: reply.status for name, reply in answers.items()}
    assert statuses == {
        "good": 200,
        "replayed": 401,
        "bad signature": 401,
        "stale": 401,
        "unsigned": 401,
        "anonymous": 401,
    }
    refusals = [reply.data["error"] for name, reply in answers.items() if name != "good"]
    assert set(refusals) == {"hivemind.entrance.authentication_failed"}


def _tampered(prepared: Prepared) -> Prepared:
    """The same request with its signature's first character changed."""
    headers = dict(prepared.headers)
    signature = headers["X-Hive-Signature"]
    headers["X-Hive-Signature"] = ("B" if signature[0] == "A" else "A") + signature[1:]
    return replace(prepared, headers=headers)


def _stale(client: GenericClient, prepared: Prepared, session: Session) -> Prepared:
    """The same request freshly signed, but over a timestamp an hour old."""
    stamp = int(time.time()) - STALE_S
    headers = client.rules.request_headers(session, "GET", prepared.target, b"", stamp)
    return replace(prepared, headers=headers)


def _unsigned(prepared: Prepared) -> Prepared:
    """The same request carrying its bearer token alone: a stolen token."""
    return replace(prepared, headers={"Authorization": prepared.headers["Authorization"]})


async def test_every_loopback_only_operation_is_a_404_on_the_remote_listener() -> None:
    # The rig's remote listener is a vpn-mode plan on a second loopback port, plain HTTP: `hive
    # serve` itself refuses a vpn bind on loopback, so a test cannot put its remote listener here.
    board = LandingBoard.load(DOCUMENT)
    async with serving(RigOptions(remote=True)) as rig:
        remote = GenericClient(rig.client(remote=True).http, board)
        local = GenericClient(rig.client().http, board)
        console, console_session = await rig.console_session()
        invite = await console.call(console_session, "POST", "/v1/entrance/invites", {"label": "x"})
        device = await _enrol(remote, None, str(invite.json()["code"]))
        path = f"/v1/entrance/pending/{device.device_id}/approve"
        body = {"name": "garden-bot", "capabilities": None, "spend_cap_usd_per_day": 1.0}
        approved = await console.call(console_session, "POST", path, body)
        assert approved.status_code == 200, approved.text
        session = await _login(remote, device, PASSWORD)
        absent = [
            operation
            for operation in board.operations()
            if "remote" not in operation.listeners or "x-hive-switch" in operation.spec
        ]
        replies = {}
        for operation in absent:
            params = dict.fromkeys(_parameters(operation.template), device.device_id)
            request = Request(operation.method, operation.template, params)
            replies[(operation.method, operation.template)] = await remote.call(request, session)
        own_listener = await local.call(Request("GET", "/v1/devices/me"), session)

    assert absent and {reply.status for reply in replies.values()} == {404}
    assert own_listener.status == 401  # A session works only on the listener it was opened on.


def _parameters(template: str) -> list[str]:
    """The names of a path template's parameters."""
    return [part[1:-1] for part in template.split("/") if part.startswith("{")]


def test_the_generic_client_imports_nothing_from_the_hive() -> None:
    imported: set[str] = set()
    for module in CLIENT_PACKAGE.glob("*.py"):
        tree = ast.parse(module.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module is not None:
                imported.add(node.module)

    roots = {name.split(".")[0] for name in imported}
    assert roots <= CLIENT_IMPORTS, roots - CLIENT_IMPORTS
    assert all(name.startswith("e2e.landing_client") for name in imported if name[:4] == "e2e.")
