"""End-to-end: roadmap phase 10's third exit criterion, a phone over the VPN, by passkey and push.

`.claude/roadmap.md` phase 10 exit criteria, third bullet: "A phone on a different network joins the
VPN, enrols by QR, waits pending until approved on the Hive Stand, then logs in with passkey plus
password, submits a goal, receives the Queen's question by web push and answers it; from the remote
listener the same phone gets a 404 on the approve route." ``hive serve``'s own Hive runs the Hive
Stand, the machine the Queen (the orchestrator) runs on: a real Queen, her Warden supervising the
Hive Stand's own Cell, and a Drone, the worker bee, over a scripted provider whose goal asks the
human one question; the Hive's SQLite file and secret store. ``e2e.remote_serve`` serves it with the
Hive Entrance (the Hive's one HTTP door) on two listeners, the remote one under the serving rig's
test vpn plan (public origin and passkey relying party ``hive.example.ts.net``), and every push
delivery goes to ``e2e.push_network``'s recorder, which plays the phone's push service. The phone
speaks only to the remote listener, its stand-in for the overlay. The operator mints an invite on
loopback; no QR decoder is locked, so the phone takes the code from the link the QR encodes, and the
test proves the QR shown at the Hive Stand is exactly that link by encoding the link again with the
Hive's own QR settings. The phone redeems the code with a new passkey (``SoftPasskey``, a software
WebAuthn authenticator reporting the remote origin), is refused a login while it waits in the Hive
Stand's pending list, and is approved there. It logs in with a passkey assertion plus the operator's
password (a session no other listener honours), subscribes to Web Push with its own keys
(``UserAgent``) under the Hive's VAPID key, and submits a goal. The push service is handed the
question's notice, VAPID-signed, which the phone decrypts with its own private key; the phone reads
the question from its inbox and answers it, then decrypts the withdrawal (same Topic, same padded
length) and its goal's completion, and the goal is finished. Last, the same phone on the remote
listener gets 404 ``not_found`` from ``POST /v1/entrance/pending/{device_id}/approve``, the route
the console approved it on, on loopback.

Fits into the Hive:
    Test infrastructure (codingrules section 14.2), not shipped.

Key invariants:
    - None: this module holds tests only.

See Also:
    - docs/adr/0041-landing-board-enrolment-two-factor-login-and-exposure.md for enrolment,
      login and the two listeners.
    - docs/adr/0042-landing-board-versioning-and-push.md for Web Push.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
import segno
from builders.entrance.landing import LandingClient, LandingSession
from builders.entrance.serving import PUBLIC_ORIGIN
from e2e.entrance_stand import ANSWER, GOAL, QUESTION, Stand, build_served, standing
from e2e.push_network import PushNetwork
from e2e.remote_serve import serve_exposed
from unit.entrance.push.support import PUSH_HOST, PUSH_URL, UserAgent

from hivemind.cli.compose.entrance import ServedHive
from hivemind.entrance.auth import SoftPasskey
from hivemind.entrance.enrol.invite import QR_ERROR_LEVEL, QR_SVG_SCALE
from hivemind.manifest import HiveManifest

pytestmark = pytest.mark.e2e

APPROVE = "/v1/entrance/pending/{device_id}/approve"  # Loopback only: never on the remote listener.


@dataclass(frozen=True, slots=True)
class _Phone:
    """The phone: its client on the remote listener, its passkey and its Web Push keys."""

    client: LandingClient
    passkey: SoftPasskey
    agent: UserAgent


def test_3_a_phone_over_the_vpn_enrols_by_qr_is_approved_at_the_stand_and_answers_by_web_push(
    tmp_path: Path,
) -> None:
    network = PushNetwork()
    # build_served runs outside any event loop, like `hive serve` itself.
    manifest, served = build_served(tmp_path, network)

    asyncio.run(_scenario(manifest, served, network))


async def _scenario(manifest: HiveManifest, served: ServedHive, network: PushNetwork) -> None:
    """Enrol, approve and log the phone in, run its goal by Web Push, then try the approve route."""
    async with standing(manifest, served, serve_exposed) as stand, _remote(stand) as http:
        client = LandingClient(http, manifest.hive.id, served.hive.clock)
        phone = _Phone(client, SoftPasskey(PUBLIC_ORIGIN), UserAgent())
        device_id = await _enrol_by_qr(stand, phone)
        session = await _approved_and_logged_in(stand, phone, device_id)
        await _goal_by_web_push(network, phone, session)
        body = {"name": "phone", "capabilities": None, "spend_cap_usd_per_day": 5.0}
        absent = await phone.client.call(session, "POST", APPROVE.format(device_id=device_id), body)

    # Not mounted on the remote listener at all: a 404, never a 403.
    assert absent.status_code == 404 and absent.json()["error"] == "hivemind.entrance.not_found"


@asynccontextmanager
async def _remote(stand: Stand) -> AsyncIterator[httpx.AsyncClient]:
    """The phone's HTTP client: the remote listener only, and no proxy between."""
    async with httpx.AsyncClient(base_url=stand.remote_url, timeout=10.0, trust_env=False) as http:
        yield http


async def _enrol_by_qr(stand: Stand, phone: _Phone) -> str:
    """Mint an invite at the Hive Stand; the phone reads its QR's link and redeems the code."""
    invite = await stand.mint("phone")
    link = str(invite["url"])
    # The QR shown is exactly the link: encoded again with the Hive's own settings, it matches.
    encoded = segno.make_qr(link, error=QR_ERROR_LEVEL).svg_inline(scale=QR_SVG_SCALE)
    device_id = await phone.client.enrol_browser(_code_in(link), phone.passkey)

    assert invite["qr_svg"] == encoded
    # An exposed Entrance's link names its public origin: the remote listener, over the overlay.
    assert link.startswith(f"{PUBLIC_ORIGIN}/enrol#")
    assert device_id == invite["device_id"]
    return device_id


def _code_in(link: str) -> str:
    """The invite code in the link's fragment, whatever else the fragment carries beside it."""
    [code] = parse_qs(urlsplit(link).fragment)["code"]
    return code


async def _approved_and_logged_in(stand: Stand, phone: _Phone, device_id: str) -> LandingSession:
    """Refused while pending, approved at the Hive Stand, then logged in on the remote listener."""
    with pytest.raises(httpx.HTTPStatusError) as pending:
        await phone.client.login_browser(device_id, phone.passkey, stand.password)
    waiting = await stand.console.call(stand.session, "GET", "/v1/entrance/pending")
    await stand.approve(device_id, "phone")
    session = await phone.client.login_browser(device_id, phone.passkey, stand.password)
    # The same signed request, on the loopback listener this session was not opened on.
    loopback = LandingClient(
        stand.console.http, stand.served.hive.manifest.hive.id, stand.served.hive.clock
    )
    elsewhere = await loopback.call(session, "GET", "/v1/devices/me")
    me = await phone.client.call(session, "GET", "/v1/devices/me")

    # Pending, its login is refused with the 401 a stranger gets, while the Stand lists it.
    assert pending.value.response.status_code == 401
    [row] = [device for device in waiting.json()["devices"] if device["id"] == device_id]
    assert (row["status"], row["key_kind"]) == ("PENDING", "passkey")
    # Opened on the remote listener, the session is honoured nowhere else.
    assert elsewhere.status_code == 401
    assert me.json()["status"] == "APPROVED" and me.json()["key_kind"] == "passkey"
    return session


async def _goal_by_web_push(network: PushNetwork, phone: _Phone, session: LandingSession) -> None:
    """Subscribe to Web Push, submit the goal, and answer its question as push announces it."""
    server_key = await _subscribed(phone, session)
    accepted = await phone.client.call(session, "POST", "/v1/goals", {"text": GOAL})
    request_id = str(accepted.json()["id"])
    # The phone reads each body as its browser would: decrypted with its own private key.
    opened = phone.agent.open
    # Latency: the Queen plans on her tick and the Drone asks on its first turn, about a second.
    asked, first = await network.delivered(PUSH_HOST, "question_waiting", opener=opened)
    inbox = await phone.client.call(session, "GET", "/v1/inbox")
    [question] = inbox.json()["questions"]
    target = f"/v1/inbox/questions/{question['id']}/answer"
    answered = await phone.client.call(session, "POST", target, {"text": ANSWER})
    withdrawn, withdrawal = await network.delivered(PUSH_HOST, "withdrawn", opener=opened)
    completed, _ = await network.delivered(PUSH_HOST, "goal_completed", opener=opened)
    finished = await phone.client.call(session, "GET", f"/v1/goals/{request_id}")

    assert accepted.status_code == 202
    # RFC 8292: signed by the VAPID key the phone subscribed with, so its push service takes it.
    assert first.headers["authorization"].endswith(f"k={server_key}")
    assert question["id"] == asked["ref"] and question["text"] == QUESTION
    assert answered.status_code == 200 and answered.json()["question_status"] == "ANSWERED"
    # The withdrawal replaces an undelivered copy (same Topic) and looks like any other notice.
    assert withdrawn["ref"] == asked["ref"]
    assert withdrawal.headers["topic"] == first.headers["topic"]
    assert len(withdrawal.content) == len(first.content)
    assert completed["ref"] == request_id and finished.json()["finished_at"] is not None


async def _subscribed(phone: _Phone, session: LandingSession) -> str:
    """Subscribe the phone to Web Push, as PushManager.subscribe does; return the VAPID key."""
    vapid = await phone.client.call(session, "GET", "/v1/push/vapid-key")
    body = {"channel": "web_push", "endpoint": PUSH_URL, "keys": phone.agent.keys.model_dump()}
    subscribed = await phone.client.call(session, "POST", "/v1/push/subscriptions", body)

    assert subscribed.status_code == 201, subscribed.text
    return str(vapid.json()["application_server_key"])
