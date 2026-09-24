"""End-to-end: a goal spoken into the phone, echoed, confirmed and run; a spoken answer resumes it.

`.claude/roadmap.md` phase 10 exit criteria, last bullet: "A goal spoken into the phone is
transcribed on the Hive Stand's local Whisper, echoed back for confirmation, confirmed, and runs;
the trail shows one `llm.call` on `TRANSCRIBER` and no audio bytes anywhere. A spoken answer to a
waiting question resumes the task without a confirmation step." Run over `hive serve`'s own
composition on real loopback sockets (a real Queen, her Hive Stand Warden and a real Drone, the
Hive's own SQLite file, the Entrance), with every slot bound to the scripted OpenAI-compatible
server of `e2e.scripted_openai`, its transcription endpoint reached through the `openai_compat`
transcription adapter exactly as a local Whisper server on the Hive Stand would be (this
environment's network policy blocks downloading real Whisper weights). An enrolled, interactive
device speaks a goal as one clip on `POST /v1/chat/audio`: it is echoed back held, and nothing is
planned until the device confirms it; the Drone then asks the human a question, and the device
answers by push-to-talk on its chat socket, which resumes the task at once; the goal finishes.
Every clip carries a distinctive byte run planted in its samples, and the test searches the
Hive's database files, every trail payload and every captured log line for it (raw, hex, base64).

Fits into the Hive:
    Test infrastructure (codingrules section 14.2), not shipped.

Key invariants:
    - None: this module holds tests only.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import pytest
import structlog
from builders.audio import marked_wav
from builders.entrance.landing import LandingClient, LandingSession
from builders.entrance.voice import speak
from e2e.entrance_stand import Stand, standing
from e2e.scripted_openai import Scenario, serve_in_thread, write_manifest
from websockets.asyncio.client import ClientConnection
from websockets.asyncio.client import connect as open_socket

from hivemind.brood_chamber import TaskFilter
from hivemind.cli.compose.entrance import ServedHive, build_served_hive
from hivemind.forage.slots import ModelSlot
from hivemind.llm import AUDIO_SECONDS_KEY
from hivemind.manifest import HiveManifest, load_manifest
from hivemind.pheromone import MAX_QUERY_LIMIT, LlmEvent, PheromoneEvent, TrailQuery
from waggle.clock import SystemClock
from waggle.ids import new_hive_id, new_node_id

pytestmark = pytest.mark.e2e

GOAL_WORDS = "Write a haiku about bees in the lavender."  # What the goal clip is heard as.
ANSWER_WORDS = "Lavender, in full bloom."  # What the answer clip is heard as.
QUESTION = "Which flower should the haiku praise?"  # What the Drone asks first.
GOAL_MARK = b"VOICEMARK-GOAL-7Q2Z-HIVE"  # Planted in the goal clip's samples.
ANSWER_MARK = b"VOICEMARK-ANSWER-9K4X-HIVE"  # Planted in the answer clip's samples.
CHAT_STREAM = "/v1/chat/stream"  # Where the phone holds its talk button.
WAIT_S = 30.0  # Generous: the whole scenario takes a few seconds locally.
_CHUNK_BYTES = 4_096  # One push-to-talk slice.
# The loopback listener on a port the system picks, limits a scripted client never meets, and the
# Hive Stand's cores pinned so the host's load average cannot zero the Drone's grant.
_EXTRA = (
    '\n[entrance]\nbind = "127.0.0.1:0"\n'
    "rate_limit_per_address = 1000\nrate_limit_per_device = 1000\n"
    "\n[hive_stand.capacity]\ncores = 64\n"
)
_PHONE = {"name": "phone", "capabilities": None, "spend_cap_usd_per_day": 50.0, "interactive": True}


@dataclass(frozen=True, slots=True)
class _Run:
    """What the scenario saw, for the assertions."""

    spoken: dict[str, Any]
    held_state: str
    tasks_before: int
    echo: list[str]
    confirmed: dict[str, Any]
    answered: dict[str, Any]
    finished: dict[str, Any]
    events: tuple[PheromoneEvent, ...]


def test_a_spoken_goal_is_echoed_confirmed_and_run_and_a_spoken_answer_resumes_it(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    scenario = Scenario(
        files=("haiku.txt",),
        question=QUESTION,
        transcript="(no marker: not a clip this test sends)",
        heard=((GOAL_MARK, GOAL_WORDS), (ANSWER_MARK, ANSWER_WORDS)),
    )
    clock = SystemClock()

    with serve_in_thread(scenario) as base_url, structlog.testing.capture_logs() as logs:
        path = write_manifest(tmp_path, base_url, (new_hive_id(clock), new_node_id(clock)))
        path.write_text(path.read_text(encoding="utf-8") + _EXTRA, encoding="utf-8")
        manifest = load_manifest(path, {})
        # Outside any event loop, as `hive serve` builds its Hive.
        served = build_served_hive(manifest, environ={}, clock=clock)
        run = asyncio.run(_speak_confirm_and_answer(manifest, served))

    _assert_the_goal_was_echoed_then_ran(run)
    _assert_one_transcription_per_clip(run.events)
    # Every structlog event dict and every stdlib record, formatted with its arguments; both
    # captures saw the run, so the search below means something.
    logged = "\n".join([*(repr(entry) for entry in logs), caplog.text])
    assert logs and caplog.records
    _assert_no_audio_and_no_words_anywhere(tmp_path, run.events, logged)


async def _speak_confirm_and_answer(manifest: HiveManifest, served: ServedHive) -> _Run:
    """Serve the Hive, approve the phone, speak the goal, confirm it, answer by push-to-talk."""
    async with standing(manifest, served) as stand:
        base = stand.loopback_url
        async with httpx.AsyncClient(base_url=base, timeout=10.0, trust_env=False) as http:
            phone = LandingClient(http, manifest.hive.id, served.hive.clock)
            session = await _enrolled_phone(stand, phone)
            spoken = await speak(phone, session, marked_wav(GOAL_MARK), "goal")
            request_id = spoken.json()["goal"]["id"]
            held_state, tasks_before, echo = await _held(stand, phone, session, request_id)
            path = f"/v1/goals/{request_id}/confirm"
            confirmed = (await phone.call(session, "POST", path)).json()
            await stand.until(stand.question_waiting)
            answered = await _answer_by_push_to_talk(stand, phone, session)
            await stand.until(lambda: stand.goal_finished(request_id))
            finished = (await phone.call(session, "GET", f"/v1/goals/{request_id}")).json()
        trail = served.hive.stores.trail
        events = await trail.query(TrailQuery(limit=MAX_QUERY_LIMIT))
    assert spoken.status_code == 202, spoken.text
    return _Run(
        spoken.json(), held_state, tasks_before, echo, confirmed, answered, finished, events
    )


async def _enrolled_phone(stand: Stand, phone: LandingClient) -> LandingSession:
    """Invite, redeem, approve (interactive, the device role's set) and log the phone in."""
    code = await stand.invite("phone")
    key = await phone.enrol(code, name="phone")
    path = f"/v1/entrance/pending/{key.device_id}/approve"
    approved = await stand.console.call(stand.session, "POST", path, _PHONE)
    assert approved.status_code == 200, approved.text
    return await phone.login(key, stand.password)


async def _held(
    stand: Stand, phone: LandingClient, session: LandingSession, request_id: str
) -> tuple[str, int, list[str]]:
    """Read the spoken goal while it waits: its state, its tasks (none), and its echo."""
    view = (await phone.call(session, "GET", f"/v1/goals/{request_id}")).json()
    chamber = stand.served.hive.stores.chamber
    tasks = await chamber.list(TaskFilter(goal_request_id=request_id))
    lines = (await phone.call(session, "GET", "/v1/chat")).json()["entries"]
    echo = [line["text"] for line in lines if line["ref"] == request_id]
    return str(view["state"]), len(tasks), echo


async def _answer_by_push_to_talk(
    stand: Stand, phone: LandingClient, session: LandingSession
) -> dict[str, Any]:
    """Hold the talk button on the phone's chat socket and answer the waiting question."""
    inbox = (await phone.call(session, "GET", "/v1/inbox")).json()
    [question] = inbox["questions"]
    assert question["text"] == QUESTION
    url = f"ws://127.0.0.1:{stand.entrance.listeners.loopback_port}{CHAT_STREAM}"
    async with open_socket(url, proxy=None, open_timeout=5.0) as socket:
        await socket.send(phone.hello(session, CHAT_STREAM))
        clip = marked_wav(ANSWER_MARK)
        for start in range(0, len(clip), _CHUNK_BYTES):
            data = base64.b64encode(clip[start : start + _CHUNK_BYTES]).decode("ascii")
            chunk = {"type": "audio_chunk", "media_type": "audio/wav", "data": data}
            await socket.send(json.dumps(chunk))
        await socket.send(json.dumps({"type": "audio_end", "intent": f"answer:{question['id']}"}))
        return await _voice_frame(socket)


async def _voice_frame(socket: ClientConnection) -> dict[str, Any]:
    """Read the chat socket until the hold's answer arrives."""
    async with asyncio.timeout(WAIT_S):
        while True:
            frame = json.loads(await socket.recv())
            if frame["type"] in {"voice", "voice_refused"}:
                return dict(frame)


def _assert_the_goal_was_echoed_then_ran(run: _Run) -> None:
    """The goal: echoed back held, nothing planned, confirmed, answered by voice, finished."""
    goal = run.spoken["goal"]
    assert (run.spoken["intent"], run.spoken["transcript"]) == ("goal", GOAL_WORDS)
    assert (goal["state"], goal["source"]) == ("AWAITING_CONFIRMATION", "spoken")
    assert (run.held_state, run.tasks_before) == ("AWAITING_CONFIRMATION", 0)
    assert len(run.echo) == 1 and GOAL_WORDS in run.echo[0]
    assert run.confirmed["state"] == "RECEIVED"
    assert run.answered["type"] == "voice", run.answered
    result = run.answered["result"]
    assert (result["intent"], result["transcript"]) == ("answer", ANSWER_WORDS)
    answered = result["answered"]
    assert (answered["question_status"], answered["task_status"]) == ("ANSWERED", "RUNNING")
    assert result["goal"] is None  # Straight through: no goal, no confirmation step.
    assert run.finished["state"] == "PLANNED" and run.finished["finished_at"] is not None


def _assert_one_transcription_per_clip(events: tuple[PheromoneEvent, ...]) -> None:
    """Two clips, two llm.call events on TRANSCRIBER, each carrying seconds and no audio."""
    calls = [event for event in events if isinstance(event, LlmEvent) and event.kind == "llm.call"]
    heard = [event for event in calls if event.slot == ModelSlot.TRANSCRIBER.value]
    assert len(heard) == 2
    assert [event.payload[AUDIO_SECONDS_KEY] for event in heard] == [1.0, 1.0]
    assert len(calls) > len(heard)  # The Queen, the Drone and the judge ran on their own slots.


def _assert_no_audio_and_no_words_anywhere(
    root: Path, events: tuple[PheromoneEvent, ...], logged: str
) -> None:
    """No planted byte run in any file under the Hive's root, any trail event or any log line."""
    forms = [form for mark in (GOAL_MARK, ANSWER_MARK) for form in _encodings(mark)]
    stored = list(_files(root))
    assert root / "data" / "hive.sqlite3" in stored  # The Hive's own database is searched.
    for path in stored:
        content = path.read_bytes()
        assert not any(form in content for form in forms), f"audio bytes in {path}"
    trail = json.dumps([event.model_dump(mode="json") for event in events]).encode()
    for text in (trail, logged.encode()):
        assert not any(form in text for form in forms)
        # The words are C2 too: the chat and the goal row hold them, never the trail or a log.
        assert GOAL_WORDS.encode() not in text and ANSWER_WORDS.encode() not in text


def _encodings(mark: bytes) -> list[bytes]:
    """The byte run as it could be stored: raw, hex, and base64 at each of its three offsets."""
    shifted = [base64.b64encode(b"\x00" * pad + mark)[4:-4] for pad in range(3)]
    return [mark, mark.hex().encode(), *[form for form in shifted if len(form) >= 16]]


def _files(root: Path) -> Iterator[Path]:
    """Every file under the Hive's root: its database and WAL, scratch, secrets, manifest."""
    return (path for path in sorted(root.rglob("*")) if path.is_file())
