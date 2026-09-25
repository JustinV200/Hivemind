"""End-to-end: `hive serve`'s composition, a program enrolled and approved over real sockets.

Roadmap step 10.5's real task: the Hive `hive serve` composes (a real Queen, her Hive Stand Warden
and a Drone on the Hive Stand's own Cell, a scripted `FakeLLMProvider`, the Hive's real SQLite
file) with the Hive Entrance on a real loopback listener. The operator's console is bootstrapped at
the Hive Stand as `hive entrance operator password` would, logs in over HTTP with its key and the
password, mints an invite and approves a program that redeemed it with an Ed25519 key; the program
logs in, submits a goal the Hive plans and finishes, says something in the chat, and reads the
Queen's reply back. Another follows a goal through the read side: the task-graph and telemetry
views over real sockets while the Warden's Drone does the work, then the tasks, Cells, Wardens,
trail and LLM reads over the Hive's own stores and the Queen's live tables. A last test proves
the exposure check refuses before anything listens.

Fits into the Hive:
    Test infrastructure (codingrules section 14.2), not shipped.

Key invariants:
    - None: this module holds tests only.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import httpx
import pytest
from builders.cli import fake_manifest
from builders.entrance.auth import PASSWORD
from builders.entrance.landing import DeviceKey, LandingClient, LandingSession
from e2e.kernel_helpers import (
    default_worker_turn,
    judge_approve_response,
    plan_response,
    single_task_plan,
    text_response,
    wait_until,
)
from websockets.asyncio.client import ClientConnection
from websockets.asyncio.client import connect as open_socket

from hivemind.cli.compose.entrance import ServedHive, build_served_hive, serve_hive
from hivemind.common.secrets import FileSecretStore
from hivemind.common.sqlite import connect
from hivemind.entrance.auth import PasswordHasher
from hivemind.entrance.enrol import (
    ConsoleDeps,
    EntranceIdentity,
    bootstrap_operator,
    unlock_console_key,
)
from hivemind.entrance.expose import ExposureRefusedError
from hivemind.entrance.runtime import HiveEntrance
from hivemind.entrance.store import SqliteEntranceStore
from hivemind.forage.slots import ModelSlot
from hivemind.llm import LLMRequest, LLMResponse
from hivemind.manifest import HiveManifest, load_manifest
from waggle.clock import SystemClock

pytestmark = pytest.mark.e2e

_GOAL = "write three haiku about bees to separate files"
_REPLY = "The haiku are written; all three are in your scratch space."
_TIMEOUT_S = 20.0  # Generous: the goal itself finishes in about a second.
# The loopback listener on a port the system picks, and limits a scripted client never meets.
_ENTRANCE = '\n[entrance]\nbind = "127.0.0.1:0"\nrate_limit_per_address = 1000\n'


def _responder(request: LLMRequest) -> LLMResponse:
    """Plan the goal, write its files, approve at the judge, and reply in the chat."""
    if request.slot is ModelSlot.QUEEN:
        # The planner's prompt carries the goal in its user section; an awake episode does not.
        if "<<<user>>>" in (request.system or ""):
            return plan_response(
                request, single_task_plan("haiku_1.txt", "haiku_2.txt", "haiku_3.txt")
            )
        decision = {"action": "REPLY", "reason": "The human asked.", "message": _REPLY}
        return text_response(json.dumps(decision))
    if request.slot is ModelSlot.WORKER:
        return default_worker_turn(request)
    if request.slot is ModelSlot.JUDGE:
        return judge_approve_response(request)
    return text_response("{}")


def _manifest(tmp_path: Path, extra: str = _ENTRANCE) -> HiveManifest:
    """Write the fake manifest with an ``[entrance]`` section, and load it."""
    path = fake_manifest(tmp_path)
    path.write_text(path.read_text(encoding="utf-8") + extra, encoding="utf-8")
    return load_manifest(path, {})


async def _bootstrap_console(manifest: HiveManifest) -> DeviceKey:
    """Set the operator password and record the console, as the Hive Stand's own command does."""
    clock = SystemClock()
    store = await SqliteEntranceStore.create(
        connect(manifest.resolve_path(manifest.hive.db)), clock
    )
    secrets = FileSecretStore(manifest.resolve_path(manifest.hive.secrets_dir))
    hasher = PasswordHasher()
    identity = EntranceIdentity(
        hive_id=manifest.hive.id, node_id=manifest.hive.node_id, actor="human"
    )
    console = await bootstrap_operator(
        ConsoleDeps(store, secrets, hasher, clock, identity), PASSWORD
    )
    return DeviceKey(console.id, await unlock_console_key(secrets, hasher, PASSWORD))


async def _approved_program(
    client: LandingClient, console: LandingSession
) -> tuple[LandingSession, str]:
    """Invite, redeem and approve a program (the device role's proposed set), then log it in."""
    invite = await client.call(console, "POST", "/v1/entrance/invites", {"label": "garden-bot"})
    assert invite.status_code == 201, invite.text
    key = await client.enrol(invite.json()["code"])
    body = {"name": "garden-bot", "capabilities": None, "spend_cap_usd_per_day": 5.0}
    approved = await client.call(
        console, "POST", f"/v1/entrance/pending/{key.device_id}/approve", body
    )
    assert approved.status_code == 200, approved.text
    return await client.login(key), key.device_id


def _until(check: Callable[[], Awaitable[bool]]) -> Awaitable[None]:
    """Wait on the real clock until ``check`` holds."""
    return wait_until(check, timeout_s=_TIMEOUT_S, poll_s=0.05)


def test_hive_serve_admits_a_program_that_runs_a_goal_and_reads_the_queens_reply(
    tmp_path: Path,
) -> None:
    # build_served_hive runs outside any event loop, like build_hive (hivemind.cli.run).
    manifest = _manifest(tmp_path)
    served = build_served_hive(
        manifest, environ={}, clock=SystemClock(), responders={"fake": _responder}
    )

    asyncio.run(_admit_and_run_a_goal(manifest, served))


async def _admit_and_run_a_goal(manifest: HiveManifest, served: ServedHive) -> None:
    """The async body of the scenario above: serve, admit, submit, chat, and check."""
    console_key = await _bootstrap_console(manifest)
    async with serve_hive(served) as entrance:
        base = f"http://localhost:{entrance.listeners.loopback_port}"
        async with httpx.AsyncClient(base_url=base, timeout=10.0) as http:
            client = LandingClient(http, manifest.hive.id, served.hive.clock)
            console = await client.login(console_key)
            program, device_id = await _approved_program(client, console)
            accepted = await client.call(program, "POST", "/v1/goals", {"text": _GOAL})
            goal = f"/v1/goals/{accepted.json()['id']}"

            async def finished() -> bool:
                view = (await client.call(program, "GET", goal)).json()
                return view["finished_at"] is not None

            await _until(finished)
            posted = await client.call(program, "POST", "/v1/chat", {"text": "How did it go?"})

            async def replied() -> bool:
                lines = (await client.call(program, "GET", "/v1/chat")).json()["entries"]
                return any(line["author"] == "queen" for line in lines)

            await _until(replied)
            chat = (await client.call(program, "GET", "/v1/chat")).json()["entries"]
            view = (await client.call(program, "GET", goal)).json()

    assert accepted.status_code == 202, accepted.text
    assert view["state"] == "PLANNED" and view["goal_id"] is not None
    assert posted.status_code == 202, posted.text
    assert [(line["author"], line["text"]) for line in chat][-2:] == [
        ("human", "How did it go?"),
        ("queen", _REPLY),
    ]
    assert device_id != console_key.device_id


def test_hive_serve_shows_a_program_its_goal_through_the_live_views_and_the_reads(
    tmp_path: Path,
) -> None:
    manifest = _manifest(tmp_path)
    served = build_served_hive(
        manifest, environ={}, clock=SystemClock(), responders={"fake": _responder}
    )

    asyncio.run(_follow_a_goal(manifest, served))


async def _open_view(
    entrance: HiveEntrance, client: LandingClient, session: LandingSession, path: str
) -> ClientConnection:
    """Open a live view on loopback with the session's signed first frame."""
    socket = await open_socket(f"ws://localhost:{entrance.listeners.loopback_port}{path}")
    await socket.send(client.hello(session, path))
    return socket


async def _frames_until(
    socket: ClientConnection, done: Callable[[dict[str, Any]], bool]
) -> list[dict[str, Any]]:
    """Read frames until one satisfies ``done``; every frame read."""
    frames: list[dict[str, Any]] = []
    async with asyncio.timeout(_TIMEOUT_S):
        while not frames or not done(frames[-1]):
            frames.append(json.loads(await socket.recv()))
    return frames


async def _follow_a_goal(manifest: HiveManifest, served: ServedHive) -> None:
    """Serve, admit a program, and follow its goal through the live views and the reads."""
    console_key = await _bootstrap_console(manifest)
    async with serve_hive(served) as entrance:
        base = f"http://localhost:{entrance.listeners.loopback_port}"
        async with httpx.AsyncClient(base_url=base, timeout=10.0) as http:
            client = LandingClient(http, manifest.hive.id, served.hive.clock)
            program, _ = await _approved_program(client, await client.login(console_key))
            hub, board = entrance.services.streams.hub, served.hive.telemetry
            before = hub.subscribers  # The Entrance's own follower of reduce orders among them.
            graph = await _open_view(entrance, client, program, "/v1/tasks/stream")
            pulse = await _open_view(entrance, client, program, "/v1/telemetry/stream")

            async def subscribed() -> bool:
                return hub.subscribers > before and board.subscribers > 0

            # Both views follow their feeds before the goal is submitted, so they miss nothing.
            await _until(subscribed)
            await client.call(program, "POST", "/v1/goals", {"text": _GOAL})
            moves = await _frames_until(graph, lambda f: f["task"]["status"] == "SUCCEEDED")
            [sample] = await _frames_until(pulse, lambda f: f["sample"]["worker_id"] is None)
            goal_id = moves[-1]["task"]["goal_id"]
            reads = {
                path: (await client.call(program, "GET", path)).json()
                for path in (
                    f"/v1/tasks?goal_id={goal_id}",
                    "/v1/cells",
                    "/v1/wardens",
                    "/v1/llm",
                    f"/v1/trail?kind=task.succeeded&subject_id={goal_id}",
                )
            }
            await graph.close()
            await pulse.close()

    [task] = reads[f"/v1/tasks?goal_id={goal_id}"]["tasks"]
    assert (task["status"], task["outcome"]["status"]) == ("SUCCEEDED", "SUCCEEDED")
    assert all("title" not in move["task"] for move in moves)
    [cell] = reads["/v1/cells"]["cells"]
    [warden] = reads["/v1/wardens"]["wardens"]
    assert (cell["kind"], cell["source"], cell["current_tasks"]) == ("REAL", "hive_stand", [])
    assert (cell["warden_id"], warden["cell_id"]) == (warden["id"], cell["id"])
    assert warden["last_heartbeat_at"] is not None and sample["sample"]["warden_id"] == warden["id"]
    assert [provider["kind"] for provider in reads["/v1/llm"]["providers"]] == ["fake"]
    [event] = reads[f"/v1/trail?kind=task.succeeded&subject_id={goal_id}"]["events"]
    assert event["subject_id"] == task["id"]


def test_hive_serve_refuses_lan_exposure_without_mutual_tls_before_listening(
    tmp_path: Path,
) -> None:
    exposed = (
        '\n[entrance]\nbind = "127.0.0.1:0"\nexpose = "lan"\nremote_bind = "192.0.2.5:8711"\n'
        'public_url = "https://hive.example.net"\nmutual_tls = false\n'
    )
    served = build_served_hive(
        _manifest(tmp_path, exposed),
        environ={},
        clock=SystemClock(),
        responders={"fake": _responder},
    )

    with pytest.raises(ExposureRefusedError):
        asyncio.run(_serve_once(served))


async def _serve_once(served: ServedHive) -> None:
    """Enter serve_hive; reaching the body at all means the refusal failed."""
    async with serve_hive(served):
        pytest.fail("the Entrance must not serve a lan exposure without mutual TLS")
