"""End-to-end push: a question reaches a phone and a program, is answered, and is withdrawn.

The dispatcher runs here over the real channels (``WebPush`` with real RFC 8291 encryption and
RFC 8292 VAPID, ``WebhookPush`` signed with the Hive's real key), the real SQLite store and the
real destination guard; only the network is simulated (``httpx.MockTransport`` plays the push
service and the program's receiver, a static table plays DNS). The phone decrypts what the push
service was handed with its own private key; the program verifies its webhook with the Hive's
public key. The Hive then restarts (a new store on the same file, a new dispatcher) before the
question is answered, and the withdrawal still reaches the phone's Web Push copy, under the same
Topic, so the push service can replace a copy the phone has not yet received.

Fits into the Hive:
    Exercises src/hivemind/entrance/push/dispatch/dispatcher.py with everything under it real
    (codingrules section 14: a real task alongside the unit tests).

Key invariants:
    - None: this module holds tests only.

See Also:
    - test_dispatcher.py beside this module for the same flows over FakePush.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import httpx
from unit.entrance.push.support import (
    HOOK_HOST,
    HOOK_URL,
    PUSH_HOST,
    PUSH_URL,
    Recorder,
    SteppingClock,
    UserAgent,
    approved_device,
    make_guard,
)

from hivemind.common.secrets import MemorySecretStore, load_or_mint_hive_signer
from hivemind.common.sqlite import connect
from hivemind.entrance.auth import b64url_decode, sha256_hex, verify_ed25519, webhook_string
from hivemind.entrance.push import (
    Admission,
    ChannelKind,
    LivePush,
    NoticeKind,
    PushChannel,
    PushChannels,
    PushDispatcher,
    PushNotice,
    SqliteSubscriptionStore,
    VapidSigner,
    WebhookPush,
    WebPush,
    audience_for,
    load_or_mint_topic_key,
    load_or_mint_vapid_key,
)
from hivemind.manifest.schema import EntrancePushSection

_QUESTION = "msg_01J8ZQ7X9K3M2N4P5Q6R7S8T9V"  # The question the Queen asked the human.
_ANSWERER = ("entrance:push", "entrance:answer")  # Both devices may answer.


@dataclass
class _Hive:
    """The pieces of one running Entrance's push side that the test inspects."""

    dispatcher: PushDispatcher
    hive_public_key: bytes
    vapid: VapidSigner


async def _start(
    db: Path, secrets: MemorySecretStore, client: httpx.AsyncClient, clock: SteppingClock
) -> _Hive:
    """Compose the push side the way the Entrance's composition root will."""
    guard = make_guard()
    signer = await load_or_mint_hive_signer(secrets)
    vapid = VapidSigner(await load_or_mint_vapid_key(secrets), "mailto:op@example.net", clock)
    stored: dict[ChannelKind, PushChannel] = {
        ChannelKind.WEBHOOK: WebhookPush(client, signer, guard, clock),
        ChannelKind.WEB_PUSH: WebPush(client, vapid, await load_or_mint_topic_key(secrets), guard),
    }
    store = await SqliteSubscriptionStore.create(connect(db), clock)
    dispatcher = PushDispatcher(
        PushChannels(live=LivePush(), stored=stored),
        store,
        Admission(EntrancePushSection(), guard),
        clock,
    )
    return _Hive(dispatcher, signer.public_key_bytes, vapid)


def _to(recorder: Recorder, host: str) -> list[httpx.Request]:
    """The requests that went to ``host`` (the pinned URL carries the address, Host the name)."""
    return [request for request in recorder.requests if request.headers["host"] == host]


def _webhook_verifies(request: httpx.Request, public_key: bytes, subscription_id: str) -> bool:
    """Verify a webhook as the program would, with the Hive's public key."""
    message = webhook_string(
        subscription_id,
        request.headers["x-hive-event-id"],
        int(request.headers["x-hive-timestamp"]),
        sha256_hex(request.content),
    )
    return verify_ed25519(public_key, message, b64url_decode(request.headers["x-hive-signature"]))


async def test_a_question_reaches_phone_and_program_and_is_withdrawn_after_a_restart(
    tmp_path: Path,
) -> None:
    clock, secrets, phone_agent = SteppingClock(), MemorySecretStore(), UserAgent()
    recorder = Recorder(default=201)
    phone = approved_device(clock, *_ANSWERER)
    program = approved_device(clock, *_ANSWERER)
    async with recorder.client() as client:
        hive = await _start(tmp_path / "hive.sqlite3", secrets, client, clock)
        web = await hive.dispatcher.register(
            phone, ChannelKind.WEB_PUSH, PUSH_URL, phone_agent.keys
        )
        hook = await hive.dispatcher.register(program, ChannelKind.WEBHOOK, HOOK_URL)
        question = PushNotice.mint(NoticeKind.QUESTION_WAITING, _QUESTION, clock)
        audience = audience_for(NoticeKind.QUESTION_WAITING, [phone, program])

        asked = await hive.dispatcher.push(question, audience)

        # The Hive restarts before the program answers; the delivery log is on disk.
        restarted = await _start(tmp_path / "hive.sqlite3", secrets, client, clock)
        withdrawn = await restarted.dispatcher.withdraw(_QUESTION)

    assert asked.delivered == withdrawn.delivered == frozenset({web.id, hook.id})
    pushed, withdrawal = _to(recorder, PUSH_HOST)
    assert json.loads(phone_agent.open(pushed.content)) == question.model_dump(mode="json")
    opened = json.loads(phone_agent.open(withdrawal.content))
    assert (opened["kind"], opened["ref"]) == ("withdrawn", _QUESTION)
    assert withdrawal.headers["topic"] == pushed.headers["topic"]
    assert len(withdrawal.content) == len(pushed.content)
    assert pushed.headers["authorization"].endswith(f"k={hive.vapid.application_server_key}")
    hooked, hook_withdrawal = _to(recorder, HOOK_HOST)
    assert json.loads(hooked.content)["event_id"] == question.event_id
    assert json.loads(hook_withdrawal.content)["kind"] == "withdrawn"
    assert _webhook_verifies(hooked, hive.hive_public_key, hook.id)
    assert _webhook_verifies(hook_withdrawal, restarted.hive_public_key, hook.id)
