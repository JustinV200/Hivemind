"""End-to-end: roadmap phase 10's fourth exit criterion, a program written from the document alone.

`.claude/roadmap.md` phase 10 exit criteria, fourth bullet: "A program using only
`docs/entrance/openapi.json` and its enrolled Ed25519 key submits a goal and receives the question
by signed webhook; the same key is refused a route its capabilities do not cover, and a goal above
its spend cap is refused pending step-up." ``hive serve``'s own composition runs the Hive Stand, the
machine the Queen (the orchestrator) runs on (``e2e.entrance_stand``: a real Queen, her Warden
supervising the Hive Stand's own Cell, and a Drone, the worker bee, over a scripted
``FakeLLMProvider`` whose goal asks the human one question; the Hive's SQLite file; the Hive
Entrance, its one HTTP door, on a real loopback listener), and every push delivery goes to
``e2e.push_network``'s recorder instead of off the host, still vetted and pinned by the Entrance's
destination guard. The program is ``e2e.landing_client``, which imports no Hive code. It mints its
own key, redeems the operator's invite, and is approved at the Hive Stand with a narrow set (the
device role's proposed set without any ``observe`` capability) and a daily spend cap of one dollar.
Logged in, it reads the Hive's key from ``GET /v1/push/hive-key`` (the key enrolment returned, as
``x-hive-signing`` says), registers a webhook and submits a goal with a small budget. The Drone's
question reaches the webhook: the body is the document's ``PushNotice``, ``X-Hive-Event-Id`` is its
event id, and the signature verifies under the Hive's key over ``hive-webhook-v1`` for this
subscription and no other. The program reads the question from its inbox and answers it; its webhook
hears the withdrawal and the goal's completion, and the goal is finished. The same key is then
refused ``GET /v1/cells`` (403 ``capability_denied`` naming ``observe``), and a goal whose budget
alone is above its daily cap is refused with 403 ``step_up_required``, reason ``over_daily_cap``,
and a pending id: the operator's console lists that held request, and the Queen's tables hold no
goal for it.

Fits into the Hive:
    Test infrastructure (codingrules section 14.2), not shipped.

Key invariants:
    - None: this module holds tests only.

See Also:
    - docs/entrance/landing-board.md, sections 4 to 10, which the program follows.
    - tests.e2e.test_landing_board_conformance for the same client's other calls and refusals.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path

import httpx
import pytest
from e2e.entrance_stand import ANSWER, GOAL, QUESTION, Stand, build_served, standing
from e2e.landing_client import (
    EVENT_ID_HEADER,
    DeviceKey,
    GenericClient,
    LandingBoard,
    Request,
    Session,
    as_object,
    sha256_hex,
)
from e2e.push_network import PushNetwork
from unit.entrance.push.support import HOOK_HOST, HOOK_URL

from hivemind.cli.compose.entrance import ServedHive
from hivemind.guard import proposed_set
from hivemind.manifest import HiveManifest
from hivemind.queen import GoalRequestQuery
from waggle.ids import DeviceId

pytestmark = pytest.mark.e2e

DOCUMENT = Path(__file__).resolve().parents[4] / "docs" / "entrance" / "openapi.json"
DAILY_CAP_USD = 1.0  # The program's daily spend cap, bound at approval.
GOAL_BUDGET_USD = 0.5  # Its goal's own budget: inside the cap, so no step-up.
OVER_CAP_BUDGET_USD = 3.0  # Above the daily cap on its own; below [entrance] step_up_spend (5).
UNCOVERED = "/v1/cells"  # Needs observe, which the operator left out of the program's set.
NOTICE_SCHEMA = {"$ref": "#/components/schemas/PushNotice"}  # What every webhook body is.
OTHER_SUBSCRIPTION = "sub_elsewhere"  # A receiver the notice was not signed for.


@dataclass(frozen=True, slots=True)
class _Enrolment:
    """A redeemed invite as the program keeps it: its key, its device and the Hive's key."""

    key: DeviceKey
    device_id: str
    hive_id: str
    hive_key_hex: str


@dataclass(frozen=True, slots=True)
class _Program:
    """A logged-in program: its session, the Hive key it pinned and its webhook subscription."""

    session: Session
    hive_key_hex: str
    subscription_id: str


def test_4_a_program_from_the_document_hears_its_question_by_webhook_and_is_held_to_its_key(
    tmp_path: Path,
) -> None:
    network = PushNetwork()
    # build_served runs outside any event loop, like `hive serve` itself.
    manifest, served = build_served(tmp_path, network)

    asyncio.run(_scenario(manifest, served, network))

    # Each notice carries its own event id, so a receiver deduping on it keeps every one.
    event_ids = [request.headers[EVENT_ID_HEADER] for request in network.to(HOOK_HOST)]
    assert event_ids and len(set(event_ids)) == len(event_ids)


async def _scenario(manifest: HiveManifest, served: ServedHive, network: PushNetwork) -> None:
    """Admit the program, run its goal by webhook, then meet the two refusals, in one Hive."""
    async with standing(manifest, served) as stand, _client(stand.loopback_url) as client:
        program = await _admitted(client, stand)
        await _goal_by_webhook(client, network, program)
        await _refused_outside_its_key(client, stand, program)


@asynccontextmanager
async def _client(base_url: str) -> AsyncIterator[GenericClient]:
    """The program's client on one listener; no proxy, since the Entrance is on this host."""
    async with httpx.AsyncClient(base_url=base_url, timeout=10.0, trust_env=False) as http:
        yield GenericClient(http, LandingBoard.load(DOCUMENT))


async def _admitted(client: GenericClient, stand: Stand) -> _Program:
    """Redeem an invite, be approved narrowly at the Hive Stand, log in, pin the key, subscribe."""
    enrolment = await _redeem(client, await stand.invite())
    await stand.approve(
        enrolment.device_id, capabilities=_program_set(stand), spend_cap_usd_per_day=DAILY_CAP_USD
    )
    session = await _login(client, enrolment, stand.password)
    hook = {"channel": "webhook", "endpoint": HOOK_URL}
    subscribed = await client.call(Request("POST", "/v1/push/subscriptions", body=hook), session)
    hive_key = await client.call(Request("GET", "/v1/push/hive-key"), session)

    assert subscribed.status == 201 and subscribed.data["channel"] == "webhook"
    # x-hive-signing: verify with the key GET /v1/push/hive-key answers, which enrolment returned.
    assert hive_key.data["public_key_hex"] == enrolment.hive_key_hex
    return _Program(session, enrolment.hive_key_hex, str(subscribed.data["id"]))


def _program_set(stand: Stand) -> list[str]:
    """The operator's grant: the device role's proposed set, less every observe capability."""
    proposed = proposed_set(stand.served.hive.enforcer.policy).as_strings()
    return [capability for capability in proposed if not capability.startswith("observe")]


async def _redeem(client: GenericClient, code: str) -> _Enrolment:
    """Mint a key and redeem ``code`` with it, signed over ``hive-enrol-v1``; now PENDING."""
    hive = await client.call(Request("GET", "/v1/enrol/hive"))
    hive_id, key = str(hive.data["hive_id"]), DeviceKey.generate()
    proof = {
        "hive_id": hive_id,
        "code_sha256": sha256_hex(code.encode("utf-8")),
        "public_key_hex": key.public_key_hex,
    }
    body = {
        "code": code,
        "public_key_hex": key.public_key_hex,
        "signature": key.sign(client.rules.message("hive-enrol-v1", proof)),
        "description": {"name": "garden-bot", "platform": "Linux", "user_agent": "garden-bot/1.0"},
    }
    redeemed = await client.call(Request("POST", "/v1/enrol/ed25519", body=body))

    assert redeemed.status == 202
    device_id, hive_key_hex = redeemed.data["device_id"], redeemed.data["hive_public_key_hex"]
    return _Enrolment(key, str(device_id), hive_id, str(hive_key_hex))


async def _login(client: GenericClient, enrolment: _Enrolment, password: str) -> Session:
    """Answer a login challenge with the device key's signature and the operator's password."""
    asked = Request("POST", "/v1/auth/challenge", body={"device_id": enrolment.device_id})
    nonce = str((await client.call(asked)).data["nonce"])
    values = {"hive_id": enrolment.hive_id, "device_id": enrolment.device_id, "challenge": nonce}
    body = {
        "device_id": enrolment.device_id,
        "nonce": nonce,
        "signature": enrolment.key.sign(client.rules.message("hive-login-v1", values)),
        "password": password,
    }
    opened = await client.call(Request("POST", "/v1/auth/login", body=body))

    assert opened.status == 201
    return Session(str(opened.data["token"]), enrolment.device_id, enrolment.key)


async def _goal_by_webhook(client: GenericClient, network: PushNetwork, program: _Program) -> None:
    """Submit the goal, hear its question by webhook, answer it, and hear it end the same way."""
    goal = {"text": GOAL, "budget_usd": GOAL_BUDGET_USD}
    accepted = await client.call(Request("POST", "/v1/goals", body=goal), program.session)
    request_id = str(accepted.data["id"])
    # Latency: the Queen plans on her tick and the Drone asks on its first turn, about a second.
    asked = await _hear(client, network, program, "question_waiting")
    question = await _only_question(client, program.session)
    target = {"question_id": str(question["id"])}
    answer = Request(
        "POST", "/v1/inbox/questions/{question_id}/answer", target, body={"text": ANSWER}
    )
    answered = await client.call(answer, program.session)
    withdrawn = await _hear(client, network, program, "withdrawn")
    completed = await _hear(client, network, program, "goal_completed")
    read = Request("GET", "/v1/goals/{request_id}", {"request_id": request_id})
    finished = await client.call(read, program.session)

    assert accepted.status == 202 and accepted.data["state"] == "RECEIVED"
    # The notice named the question and nothing of it; the inbox holds the words.
    assert question["id"] == asked["ref"] and question["text"] == QUESTION
    assert answered.status == 200 and answered.data["question_status"] == "ANSWERED"
    assert withdrawn["ref"] == asked["ref"] and completed["ref"] == request_id
    assert finished.data["state"] == "PLANNED" and finished.data["finished_at"] is not None


async def _only_question(client: GenericClient, session: Session) -> dict[str, object]:
    """Read the inbox (C2, which the program's set holds) and return its one question."""
    inbox = await client.call(Request("GET", "/v1/inbox"), session)
    questions = inbox.data["questions"]
    assert isinstance(questions, list) and len(questions) == 1
    return as_object(questions[0], "the question")


async def _hear(
    client: GenericClient, network: PushNetwork, program: _Program, kind: str
) -> dict[str, object]:
    """Wait for the webhook's first ``kind`` notice; take it as the program's receiver does."""
    _, delivery = await network.delivered(HOOK_HOST, kind)
    notice = as_object(json.loads(delivery.content), "a webhook body")
    client.board.check(notice, NOTICE_SCHEMA, "a webhook body")
    # HTTP header names are case-insensitive: httpx's mapping reads them as a receiver would.
    headers, key = delivery.headers, program.hive_key_hex
    signed = client.rules.webhook_holds(key, program.subscription_id, headers, delivery.content)
    replayed = client.rules.webhook_holds(key, OTHER_SUBSCRIPTION, headers, delivery.content)

    assert signed and not replayed
    assert headers[EVENT_ID_HEADER] == notice["event_id"]
    return notice


async def _refused_outside_its_key(client: GenericClient, stand: Stand, program: _Program) -> None:
    """The same key: refused a route it holds no capability for, and a goal above its cap."""
    uncovered = await client.call(Request("GET", UNCOVERED), program.session)
    over = {"text": "tidy the whole garden", "budget_usd": OVER_CAP_BUDGET_USD}
    held = await client.call(Request("POST", "/v1/goals", body=over), program.session)
    listed = await stand.console.call(stand.session, "GET", "/v1/entrance/confirmations")
    device = DeviceId(program.session.device_id)
    committed = await stand.served.hive.stores.goal_requests.list_requests(
        GoalRequestQuery(device_id=device)
    )

    # Its set holds no observe: refused, naming the capability the route needs.
    assert uncovered.status == 403 and uncovered.data["capability"] == "observe"
    assert uncovered.data["error"] == "hivemind.entrance.capability_denied"
    # A program cannot step up: its goal is held for a person, named by its pending id.
    assert held.status == 403 and held.data["error"] == "hivemind.entrance.step_up_required"
    assert held.data["reason"] == "over_daily_cap"
    pending_id = str(held.data["pending_id"])
    assert pending_id.startswith("pend_")
    [confirmation] = [row for row in listed.json()["confirmations"] if row["id"] == pending_id]
    assert (confirmation["device_id"], confirmation["action"]) == (device, "goal")
    assert confirmation["status"] == "PENDING"
    assert confirmation["payload"]["budget_usd"] == OVER_CAP_BUDGET_USD
    # Held, not submitted: the Queen's table has the program's first goal alone.
    assert [request.budget_usd for request in committed] == [GOAL_BUDGET_USD]
