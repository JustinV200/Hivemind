"""End-to-end: a program written from the committed OpenAPI document alone uses the Landing Board.

Roadmap step 10.5c's conformance test. The Landing Board (the Hive Entrance's versioned API,
ADR-0034) promises that a third-party program written from ``docs/entrance/openapi.json`` alone
works. ``e2e.landing_client`` is that program: it imports no Hive code, finds every operation by
method and path in the committed document, signs from ``x-hive-signing`` alone, and accepts a
reply only when its status is declared and its body matches the declared schema. Against
``hive serve``'s own composition over real loopback sockets (``e2e.entrance_stand``: a real Queen,
Warden and Drone over a scripted provider, the operator at the Hive Stand), the client reads the
Hive's id, redeems an invite (a second redemption is refused) and is refused while pending, logs
in once approved, subscribes to the live push stream with a signed first frame, submits a goal
(202), answers the question the Queen routed to the human (a second answer is a 409), sees it
withdrawn and the goal completed by push, reads the goal back, chats with the Queen and reads the
chat and its stream, then logs out. Refusals are checked the same way: a bad signature, a replayed
nonce, a stale timestamp, a bare token and a capability the device lacks. Against the builders'
serving rig, whose remote listener runs a ``vpn`` plan on a test address and whose push service
records deliveries, every loopback-only operation is a 404 on the remote listener, and a webhook
verifies under the Hive key.

Fits into the Hive:
    Test infrastructure (codingrules section 14.2), not shipped.

Key invariants:
    - None: this module holds tests only.
"""

from __future__ import annotations

import ast
import asyncio
import json
import sys
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
from pathlib import Path

import httpx
import pytest
from builders.entrance import PASSWORD
from builders.entrance.serving import RigOptions, ServingRig, serving
from e2e.entrance_stand import (
    ANSWER,
    CHAT,
    GOAL,
    QUESTION,
    REPLY,
    WAIT_S,
    Stand,
    build_served,
    standing,
)
from e2e.landing_client import (
    EVENT_ID_HEADER,
    DeviceKey,
    GenericClient,
    LandingBoard,
    Prepared,
    Request,
    Session,
    StreamFeed,
    sha256_hex,
)
from unit.entrance.push.support import HOOK_HOST, HOOK_URL

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
AUTH_FAILED = "hivemind.entrance.authentication_failed"  # Every session refusal's one code.

# Approves a pending device at the Hive Stand, given its id.
type Approve = Callable[[str], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class _Device:
    """An enrolled device as the program knows it: key, id, the Hive and its pinned key."""

    key: DeviceKey
    device_id: str
    hive_id: str
    hive_key_hex: str


@asynccontextmanager
async def _client(base_url: str) -> AsyncIterator[GenericClient]:
    """A generic client on one listener; no proxy, since the Entrance is on this host."""
    async with httpx.AsyncClient(base_url=base_url, timeout=10.0, trust_env=False) as http:
        yield GenericClient(http, LandingBoard.load(DOCUMENT))


def _redemption(client: GenericClient, key: DeviceKey, hive_id: str, code: str) -> Request:
    """The request redeeming ``code`` with ``key``, signed over ``hive-enrol-v1``."""
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
    return Request("POST", "/v1/enrol/ed25519", body=body)


async def _enrol(client: GenericClient, code: str, approve: Approve) -> _Device:
    """Redeem ``code`` with a fresh key: refused again and while pending, then approved."""
    hive = await client.call(Request("GET", "/v1/enrol/hive"))
    hive_id, key = str(hive.data["hive_id"]), DeviceKey.generate()
    redeemed = await client.call(_redemption(client, key, hive_id, code))
    again = await client.call(_redemption(client, DeviceKey.generate(), hive_id, code))
    device_id = str(redeemed.data["device_id"])
    asked = Request("POST", "/v1/auth/challenge", body={"device_id": device_id})
    pending = await client.call(asked)
    await approve(device_id)

    assert redeemed.status == 202
    assert (again.status, again.data["error"]) == (403, "hivemind.entrance.enrolment_refused")
    assert (pending.status, pending.data["error"]) == (401, AUTH_FAILED)
    return _Device(key, device_id, hive_id, str(redeemed.data["hive_public_key_hex"]))


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
        device = await _enrol(client, await stand.invite(), stand.approve)
        session = await _login(client, device, stand.password)
        me = await client.call(Request("GET", "/v1/devices/me"), session)
        # One subscription for the whole run: a closed socket's attachment may linger briefly.
        async with _subscribed(client, stand, session) as push:
            request_id = await _submit_and_answer(client, push, session)
            await _chat(client, push, session)
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


async def _submit_and_answer(client: GenericClient, push: StreamFeed, session: Session) -> str:
    """Submit the goal, answer its question as push announces it; return the goal request id."""
    accepted = await client.call(Request("POST", "/v1/goals", body={"text": GOAL}), session)
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
    twice = await client.call(answer, session)
    withdrawn = await push.until("withdrawn", str(asked["ref"]))
    completed = await push.until("goal_completed", str(accepted.data["id"]))

    assert accepted.status == 202 and accepted.data["state"] == "RECEIVED"
    assert (question["text"], question["options"]) == (QUESTION, [])
    assert answered.data["question_status"] == "ANSWERED"
    assert twice.status == 409  # Answered already: the item moved on.
    assert withdrawn["ref"] == asked["ref"] and completed["ref"] == accepted.data["id"]
    return str(accepted.data["id"])


async def _chat(client: GenericClient, push: StreamFeed, session: Session) -> None:
    """Say something to the Queen, hear by push that she replied, read the chat and its stream."""
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


def test_a_bad_signature_a_replay_a_stale_timestamp_and_a_missing_capability_are_refused(
    tmp_path: Path,
) -> None:
    manifest, served = build_served(tmp_path)

    asyncio.run(_refusals(manifest, served))


async def _refusals(manifest: HiveManifest, served: ServedHive) -> None:
    """Send one good signed request, then each way a request is refused."""
    async with standing(manifest, served) as stand, _client(stand.loopback_url) as client:
        device = await _enrol(client, await stand.invite(), stand.approve)
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
        # The device role's proposed set never holds entrance:steward.
        denied = await client.call(Request("GET", "/v1/entrance/pending"), session)

    statuses = {name: reply.status for name, reply in answers.items()}
    assert statuses == {"good": 200} | dict.fromkeys(list(statuses)[1:], 401)
    assert {reply.data["error"] for name, reply in answers.items() if name != "good"} == {
        AUTH_FAILED
    }
    assert (denied.status, denied.data["capability"]) == (403, "entrance:steward")
    assert denied.data["error"] == "hivemind.entrance.capability_denied"


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


async def _rig_device(rig: ServingRig, client: GenericClient) -> tuple[_Device, Session]:
    """Enrol ``client``'s device in a serving rig, approved by the rig's console; log it in."""
    console, console_session = await rig.console_session()
    invite = await console.call(console_session, "POST", "/v1/entrance/invites", {"label": "bot"})

    async def approve(device_id: str) -> None:
        """Approve at the Hive Stand with the device role's proposed set."""
        path = f"/v1/entrance/pending/{device_id}/approve"
        body = {"name": "garden-bot", "capabilities": None, "spend_cap_usd_per_day": 1.0}
        approved = await console.call(console_session, "POST", path, body)
        assert approved.status_code == 200, approved.text

    device = await _enrol(client, str(invite.json()["code"]), approve)
    return device, await _login(client, device, PASSWORD)


async def test_every_loopback_only_operation_is_a_404_on_the_remote_listener() -> None:
    # The rig's remote listener is a vpn-mode plan on a second loopback port, plain HTTP: `hive
    # serve` itself refuses a vpn bind on loopback, so a test cannot put its remote listener there.
    board = LandingBoard.load(DOCUMENT)
    async with serving(RigOptions(remote=True)) as rig:
        remote = GenericClient(rig.client(remote=True).http, board)
        device, session = await _rig_device(rig, remote)
        # Loopback-only, or mounted only under a manifest switch the rig leaves off.
        absent = [
            operation
            for operation in board.operations()
            if "remote" not in operation.listeners or "x-hive-switch" in operation.spec
        ]
        replies = [
            await remote.call(
                Request(
                    op.method, op.template, dict.fromkeys(_names(op.template), device.device_id)
                ),
                session,
            )
            for op in absent
        ]
        local = GenericClient(rig.client().http, board)
        elsewhere = await local.call(Request("GET", "/v1/devices/me"), session)

    assert absent and {reply.status for reply in replies} == {404}
    assert {reply.data["error"] for reply in replies} == {"hivemind.entrance.not_found"}
    assert elsewhere.status == 401  # A session works only on the listener it was opened on.


def _names(template: str) -> list[str]:
    """The names of a path template's parameters."""
    return [part[1:-1] for part in template.split("/") if part.startswith("{")]


async def test_a_webhook_notice_verifies_under_the_hive_key_and_carries_its_event_id() -> None:
    board = LandingBoard.load(DOCUMENT)
    async with serving() as rig:
        client = GenericClient(rig.client().http, board)
        device, session = await _rig_device(rig, client)
        hook = {"channel": "webhook", "endpoint": HOOK_URL}
        subscribed = await client.call(
            Request("POST", "/v1/push/subscriptions", body=hook), session
        )
        hive_key = await client.call(Request("GET", "/v1/push/hive-key"), session)
        # Another device asking to join is a security event, pushed to every other device.
        console, console_session = await rig.console_session()
        invite = await console.call(console_session, "POST", "/v1/entrance/invites", {"label": "x"})
        await rig.client().enrol(str(invite.json()["code"]))
        await rig.until(lambda: any(_to_hook(rig)), timeout_s=WAIT_S)
        delivery = _to_hook(rig)[0]

    notice = json.loads(delivery.content)
    board.check(notice, {"$ref": "#/components/schemas/PushNotice"}, "a webhook body")
    # HTTP header names are case-insensitive: httpx's mapping reads them as a receiver would.
    headers = delivery.headers
    subscription_id = str(subscribed.data["id"])
    assert hive_key.data["public_key_hex"] == device.hive_key_hex
    assert client.rules.webhook_holds(
        device.hive_key_hex, subscription_id, headers, delivery.content
    )
    assert not client.rules.webhook_holds(
        device.hive_key_hex, "sub_other", headers, delivery.content
    )
    assert (notice["kind"], notice["event_id"]) == ("security_event", headers[EVENT_ID_HEADER])


def _to_hook(rig: ServingRig) -> list[httpx.Request]:
    """Every delivery the rig's fake push service received for the webhook's host."""
    # A delivery connects to the address the destination guard pinned; Host names the receiver.
    return [
        request for request in rig.push_service.requests if request.headers["host"] == HOOK_HOST
    ]


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
