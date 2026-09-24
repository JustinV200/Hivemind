"""Run `hive serve`'s composition for a Landing Board test, and be the operator at its Hive Stand.

The Landing Board conformance tests (roadmap step 10.5c) drive a real Hive Entrance (the Hive's one
HTTP door) with a client that knows nothing but the committed OpenAPI document. Someone still has to
stand at the Hive Stand (the machine the Queen runs on), and that is this module: it builds the Hive
exactly as `hive serve` does (a real Queen, her Hive Stand Warden and a Drone over a scripted
`FakeLLMProvider`, the Hive's own SQLite file, the Entrance on a real loopback socket), sets the
operator password and records the console device as `hive entrance operator password` would, and
logs the console in over HTTP to mint invites and approve devices, as the operator does on loopback.
The scripted goal asks the human one question before it writes its files, and the Queen answers a
chat message with a fixed reply, so a client can exercise all three calls: submit, subscribe,
answer.

Fits into the Hive:
    Test infrastructure (codingrules section 14.5), not shipped. Used by
    tests/e2e/test_landing_board_conformance.py and tests/e2e/test_landing_board_guide.py.

Key invariants:
    - The operator password is minted per run, never written to a committed file.
    - The Entrance and every HTTP client are closed when the block exits.
"""

from __future__ import annotations

import asyncio
import json
import secrets
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path

import httpx
from builders.cli import fake_manifest
from builders.entrance.landing import DeviceKey, LandingClient, LandingSession
from e2e.kernel_helpers import (
    judge_approve_response,
    plan_response,
    single_task_plan,
    text_response,
    tool_response,
    tool_round_count,
    write_call,
)

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
from hivemind.entrance.runtime import HiveEntrance
from hivemind.entrance.store import SqliteEntranceStore
from hivemind.forage.slots import ModelSlot
from hivemind.llm import LLMRequest, LLMResponse
from hivemind.manifest import HiveManifest, load_manifest
from waggle.clock import SystemClock

GOAL = "write three haiku about bees to separate files"
QUESTION = "Which season should the haiku be about?"
ANSWER = "spring"
CHAT = "How did it go?"
REPLY = "The haiku are written; all three are about spring."
FILES = ("haiku_1.txt", "haiku_2.txt", "haiku_3.txt")
WAIT_S = 20.0  # Generous: a goal with one question finishes in about two seconds locally.
_POLL_S = 0.02  # How often a wait re-reads the state it waits on.
_PASSWORD_BYTES = 18  # A fresh operator password per run: 24 characters, above the 12 minimum.
# The loopback listener on a port the system picks, and limits a scripted client never meets.
_ENTRANCE = (
    '\n[entrance]\nbind = "127.0.0.1:0"\n'
    "rate_limit_per_address = 1000\nrate_limit_per_device = 1000\n"
)

__all__ = [
    "ANSWER",
    "CHAT",
    "FILES",
    "GOAL",
    "QUESTION",
    "REPLY",
    "WAIT_S",
    "Stand",
    "build_served",
    "standing",
]


def build_served(tmp_path: Path) -> tuple[HiveManifest, ServedHive]:
    """Write the manifest and build the Hive `hive serve` runs; call outside any event loop.

    Args:
        tmp_path: The test's own directory; the Hive's data and scratch live under it.

    Returns:
        The manifest and the Hive, not yet started.
    """
    path = fake_manifest(tmp_path)
    path.write_text(path.read_text(encoding="utf-8") + _ENTRANCE, encoding="utf-8")
    manifest = load_manifest(path, {})
    served = build_served_hive(
        manifest, environ={}, clock=SystemClock(), responders={"fake": _responder}
    )
    return manifest, served


@dataclass(frozen=True, slots=True)
class Stand:
    """The running Entrance, and the operator's console logged in on its loopback listener."""

    entrance: HiveEntrance
    served: ServedHive
    console: LandingClient
    session: LandingSession
    password: str = field(repr=False)

    @property
    def loopback_url(self) -> str:
        """The loopback listener's base URL, by address."""
        return f"http://127.0.0.1:{self.entrance.listeners.loopback_port}"

    async def invite(self, label: str = "garden-bot") -> str:
        """Mint an invite at the Hive Stand, as `hive entrance invite` does.

        Args:
            label: What the operator calls the device.

        Returns:
            The invite code, as the Hive Stand shows it.
        """
        body = {"label": label}
        minted = await self.console.call(self.session, "POST", "/v1/entrance/invites", body)
        assert minted.status_code == 201, minted.text
        return str(minted.json()["code"])

    async def approve(self, device_id: str, name: str = "garden-bot") -> None:
        """Approve a pending device at the Hive Stand with the device role's proposed set.

        Args:
            device_id: The device that redeemed an invite.
            name: The name the operator binds.
        """
        body = {"name": name, "capabilities": None, "spend_cap_usd_per_day": 5.0}
        path = f"/v1/entrance/pending/{device_id}/approve"
        approved = await self.console.call(self.session, "POST", path, body)
        assert approved.status_code == 200, approved.text

    async def until(self, check: Callable[[], bool | Awaitable[bool]]) -> None:
        """Wait on the Hive's own state until ``check`` holds, or fail after ``WAIT_S``.

        Args:
            check: Reads state (the Entrance's registries, the Hive's stores, a console call).
        """
        async with asyncio.timeout(WAIT_S):
            while True:
                held = check()
                if held if isinstance(held, bool) else await held:
                    return
                await asyncio.sleep(_POLL_S)

    async def question_waiting(self) -> bool:
        """Return whether the human's inbox holds a question (the console reads it)."""
        inbox = await self.console.call(self.session, "GET", "/v1/inbox")
        return bool(inbox.json()["questions"])

    async def goal_finished(self, request_id: str) -> bool:
        """Return whether a goal request's goal has finished, from the Queen's own table."""
        request = await self.served.hive.stores.goal_requests.get(request_id)
        return request.finished_at is not None

    async def replied(self) -> bool:
        """Return whether the Queen has written a reply into the chat."""
        chat = await self.console.call(self.session, "GET", "/v1/chat")
        return any(line["author"] == "queen" for line in chat.json()["entries"])


@asynccontextmanager
async def standing(manifest: HiveManifest, served: ServedHive) -> AsyncIterator[Stand]:
    """Bootstrap the operator, serve the Hive, and log the console in until the block exits.

    Args:
        manifest: The manifest ``build_served`` wrote.
        served: The Hive ``build_served`` built.

    Yields:
        The stand: the running Entrance and the logged-in console.
    """
    password = secrets.token_urlsafe(_PASSWORD_BYTES)
    console_key = await _bootstrap_console(manifest, password)
    async with serve_hive(served) as entrance:
        base = f"http://127.0.0.1:{entrance.listeners.loopback_port}"
        async with httpx.AsyncClient(base_url=base, timeout=10.0, trust_env=False) as http:
            console = LandingClient(http, manifest.hive.id, served.hive.clock)
            session = await console.login(console_key, password)
            yield Stand(entrance, served, console, session, password)


async def _bootstrap_console(manifest: HiveManifest, password: str) -> DeviceKey:
    """Set the operator password and record the console, as the Hive Stand's own command does."""
    clock = SystemClock()
    store = await SqliteEntranceStore.create(
        connect(manifest.resolve_path(manifest.hive.db)), clock
    )
    vault = FileSecretStore(manifest.resolve_path(manifest.hive.secrets_dir))
    hasher = PasswordHasher()
    identity = EntranceIdentity(
        hive_id=manifest.hive.id, node_id=manifest.hive.node_id, actor="human"
    )
    console = await bootstrap_operator(ConsoleDeps(store, vault, hasher, clock, identity), password)
    return DeviceKey(console.id, await unlock_console_key(vault, hasher, password))


def _responder(request: LLMRequest) -> LLMResponse:
    """Plan the goal, ask the human once, write the files, approve at the judge, reply in chat."""
    if request.slot is ModelSlot.QUEEN:
        # The planner's prompt carries the goal in its user section; an awake episode does not.
        if "<<<user>>>" in (request.system or ""):
            return plan_response(request, single_task_plan(*FILES))
        decision = {"action": "REPLY", "reason": "The human asked.", "message": REPLY}
        return text_response(json.dumps(decision))
    if request.slot is ModelSlot.WORKER:
        return _worker_turn(request)
    if request.slot is ModelSlot.JUDGE:
        return judge_approve_response(request)
    return text_response("{}")


def _worker_turn(request: LLMRequest) -> LLMResponse:
    """The Drone's script: ask the human which season, then write every file, then stop."""
    rounds = tool_round_count(request)
    if rounds == 0:
        return tool_response(request, (("ask_1", "ask", {"text": QUESTION}),))
    if rounds == 1:
        return tool_response(request, tuple(write_call(name) for name in FILES))
    return text_response("Three haiku written.")
