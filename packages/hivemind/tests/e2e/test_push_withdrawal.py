"""End-to-end: roadmap step 10.5b's named test, a question answered on one device, withdrawn on all.

`.claude/roadmap.md` step 10.5b: "A question answered on any device is withdrawn from every other; a
test asks from the CLI, answers by webhook, and asserts the web-push copy is withdrawn." ``hive
serve``'s own composition runs the Hive Stand, the machine the Queen (the orchestrator) runs on
(``e2e.entrance_stand``: a real Queen, her Warden supervising the Hive Stand's own Cell, and a
Drone, the worker bee, over a scripted provider whose goal asks the human one question; the Hive's
SQLite file; the Hive Entrance, its one HTTP door, on a real loopback listener), and every push
delivery goes to ``e2e.push_network``'s recorder, which plays both a program's webhook receiver and
a phone's push service. Three devices are enrolled and approved at the Hive Stand: a laptop, through
``hive remote enrol`` in its own config directory; a program with an Ed25519 key, which registers a
webhook; and a phone with a passkey, which subscribes to Web Push with its own keys. The laptop's
``hive run --remote`` submits the goal and follows it. The Drone's question reaches the program's
webhook, signed by the Hive's key for that subscription, and the phone's push service, encrypted to
the phone's key. The program reads the question from its inbox and answers it through the Landing
Board (the Entrance's API). The phone's push service is then handed the withdrawal, which the phone
decrypts with its own key: the question's ref, under the question's Topic, so a copy not yet shown
is replaced. The program's webhook hears the withdrawal too, and the laptop's follow prints the
question and the goal's end.

Fits into the Hive:
    Test infrastructure (codingrules section 14.2), not shipped.

Key invariants:
    - None: this module holds tests only.

See Also:
    - docs/adr/0034-landing-board-versioning-and-push.md, "Answered anywhere, withdrawn
      everywhere".
    - tests.unit.entrance.push.dispatch.test_end_to_end for the dispatcher alone, restart included.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import pytest
from builders.entrance.landing import LandingClient, LandingSession
from builders.entrance.stand import Terminal, laptop_terminal
from e2e.entrance_stand import ANSWER, GOAL, QUESTION, Stand, build_served, standing
from e2e.push_network import Opener, PushNetwork, as_sent
from typer.testing import Result
from unit.entrance.push.support import HOOK_HOST, HOOK_URL, PUSH_HOST, PUSH_URL, UserAgent

from hivemind.cli.compose.entrance import ServedHive
from hivemind.entrance.auth import (
    SoftPasskey,
    b64url_decode,
    sha256_hex,
    verify_ed25519,
    webhook_string,
)
from hivemind.entrance.runtime import LOOPBACK_RP_ID
from hivemind.manifest import HiveManifest

pytestmark = pytest.mark.e2e

FOLLOW_TIMEOUT_S = 60  # `hive run --remote` stops following then; the goal ends in seconds.


@dataclass(frozen=True, slots=True)
class _Program:
    """The program: its client and session, its webhook, and the Hive key it verifies with."""

    client: LandingClient
    session: LandingSession
    subscription_id: str
    hive_key: bytes


@dataclass(frozen=True, slots=True)
class _Run:
    """What the scenario leaves to check: the laptop's follow, the question, the program."""

    followed: Result
    question_id: str
    program: _Program


def test_a_question_asked_from_the_cli_and_answered_by_webhook_is_withdrawn_from_web_push(
    tmp_path: Path,
) -> None:
    network, phone = PushNetwork(), UserAgent()
    # build_served runs outside any event loop, like `hive serve` itself.
    manifest, served = build_served(tmp_path / "stand", network)
    laptop = laptop_terminal(tmp_path / "laptop")

    run = asyncio.run(_scenario(manifest, served, network, laptop, phone))

    printed = run.followed.output
    assert run.followed.exit_code == 0, printed
    assert f"queen asks [{run.question_id}]: {QUESTION}" in printed and " finished at " in printed
    # The web-push copy: pushed once, then withdrawn under the same Topic, so it is replaced.
    pushed = _about(network, PUSH_HOST, run.question_id, phone.open)
    assert [notice["kind"] for notice, _ in pushed] == ["question_waiting", "withdrawn"]
    assert pushed[1][1].headers["topic"] == pushed[0][1].headers["topic"]
    # The program that answered was told as well, each notice signed for its subscription.
    hooked = _about(network, HOOK_HOST, run.question_id, as_sent)
    assert [notice["kind"] for notice, _ in hooked] == ["question_waiting", "withdrawn"]
    assert all(_signed(run.program, delivery) for _, delivery in hooked)


async def _scenario(
    manifest: HiveManifest,
    served: ServedHive,
    network: PushNetwork,
    laptop: Terminal,
    phone: UserAgent,
) -> _Run:
    """Admit the three devices, run the goal from the laptop, answer it from the program."""
    async with standing(manifest, served) as stand:
        program = await _program(stand)
        await _phone(stand, phone)
        await _laptop(stand, laptop)
        run = ("run", "--remote", GOAL, "--password-stdin", "--timeout", str(FOLLOW_TIMEOUT_S))
        # The laptop's terminal follows the goal while the program answers; both end together.
        # Latency: the follow ends seconds after the answer, or at its own timeout.
        question_id, followed = await asyncio.gather(
            _answer_by_webhook(network, program), laptop.hive(*run, stdin=f"{stand.password}\n")
        )
        # The follow may end before the outbox sends both withdrawals: wait for them, then stop.
        await network.delivered(PUSH_HOST, "withdrawn", question_id, phone.open)
        await network.delivered(HOOK_HOST, "withdrawn", question_id)
    return _Run(followed, question_id, program)


async def _program(stand: Stand) -> _Program:
    """Enrol a program with an Ed25519 key, approve it at the Hive Stand, register its webhook."""
    hive = stand.served.hive
    client = LandingClient(stand.console.http, hive.manifest.hive.id, hive.clock)
    key = await client.enrol(await stand.invite("garden-bot"))
    await stand.approve(key.device_id, "garden-bot")
    session = await client.login(key, stand.password)
    hook = {"channel": "webhook", "endpoint": HOOK_URL}
    subscribed = await client.call(session, "POST", "/v1/push/subscriptions", hook)
    hive_key = await client.call(session, "GET", "/v1/push/hive-key")

    assert subscribed.status_code == 201, subscribed.text
    public_key = bytes.fromhex(hive_key.json()["public_key_hex"])
    return _Program(client, session, str(subscribed.json()["id"]), public_key)


async def _phone(stand: Stand, agent: UserAgent) -> None:
    """Enrol a phone with a passkey, approve it at the Hive Stand, subscribe it to Web Push."""
    hive = stand.served.hive
    client = LandingClient(stand.console.http, hive.manifest.hive.id, hive.clock)
    # A browser on the Hive Stand reaches loopback by name: the relying party is localhost.
    passkey = SoftPasskey(f"http://{LOOPBACK_RP_ID}:{stand.entrance.listeners.loopback_port}")
    device_id = await client.enrol_browser(await stand.invite("phone"), passkey)
    await stand.approve(device_id, "phone")
    session = await client.login_browser(device_id, passkey, stand.password)
    body = {"channel": "web_push", "endpoint": PUSH_URL, "keys": agent.keys.model_dump()}
    subscribed = await client.call(session, "POST", "/v1/push/subscriptions", body)

    assert subscribed.status_code == 201, subscribed.text


async def _laptop(stand: Stand, laptop: Terminal) -> None:
    """Enrol the laptop with `hive remote enrol` from the invite link; approve it at the Stand."""
    invite = await stand.mint("laptop")
    hive_id = stand.served.hive.manifest.hive.id
    enrol = ("remote", "enrol", str(invite["url"]), "--hive", hive_id, "--name", "laptop")
    enrolled = await laptop.hive(*enrol)
    await stand.approve(str(invite["device_id"]), "laptop")

    assert enrolled.exit_code == 0, enrolled.output


async def _answer_by_webhook(network: PushNetwork, program: _Program) -> str:
    """Hear the question by webhook, read it from the inbox, answer it; return its id."""
    # Latency: the Queen plans on her tick and the Drone asks on its first turn, about a second.
    asked, delivery = await network.delivered(HOOK_HOST, "question_waiting")
    inbox = await program.client.call(program.session, "GET", "/v1/inbox")
    [question] = inbox.json()["questions"]
    target = f"/v1/inbox/questions/{question['id']}/answer"
    answered = await program.client.call(program.session, "POST", target, {"text": ANSWER})

    assert _signed(program, delivery)
    assert question["id"] == asked["ref"] and question["text"] == QUESTION
    assert answered.status_code == 200 and answered.json()["question_status"] == "ANSWERED"
    return str(question["id"])


def _about(
    network: PushNetwork, host: str, question_id: str, opener: Opener
) -> list[tuple[dict[str, Any], httpx.Request]]:
    """Every notice ``host`` was sent about the question, oldest first, read as it reads them."""
    read = [(json.loads(opener(request.content)), request) for request in network.to(host)]
    return [(notice, request) for notice, request in read if notice["ref"] == question_id]


def _signed(program: _Program, delivery: httpx.Request) -> bool:
    """Whether a webhook delivery is the Hive's, for this program's subscription."""
    headers = delivery.headers
    message = webhook_string(
        program.subscription_id,
        headers["x-hive-event-id"],
        int(headers["x-hive-timestamp"]),
        sha256_hex(delivery.content),
    )
    return verify_ed25519(program.hive_key, message, b64url_decode(headers["x-hive-signature"]))
