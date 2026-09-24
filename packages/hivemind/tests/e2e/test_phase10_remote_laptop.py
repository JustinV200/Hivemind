"""End-to-end: roadmap phase 10's second exit criterion, a laptop enrolled against ``hive serve``.

`.claude/roadmap.md` phase 10 exit criteria, second bullet: "`hive run --remote` and `hive inbox
--remote` work against `hive serve` from an enrolled laptop; unauthenticated requests, and requests
from a device that is pending, locked or revoked, are rejected." ``hive serve``'s own composition
runs the Hive Stand: a real Queen, her Hive Stand Warden and a Drone on the Hive Stand's own Cell
over a scripted ``FakeLLMProvider``, the Hive's real SQLite file, and the Entrance on a real
loopback listener. The operator sets the password with ``hive entrance operator password`` and
mints an invite with ``hive entrance invite``; a second config directory (the laptop) enrols with
``hive remote enrol`` and is approved with ``hive entrance approve``. The laptop's ``hive run
--remote`` is a real ``hive`` process following the goal while the laptop's other terminal answers
the Drone's question with ``hive inbox --remote answer``: the goal finishes, its task SUCCEEDED.
The second test refuses a request without credentials, a stolen session token signed with another
key and a replayed request, then the same laptop pending, locked by five wrong passwords (and
unlocked on loopback), and revoked.

Fits into the Hive:
    Test infrastructure (codingrules section 14.2), not shipped.

Key invariants:
    - None: this module holds tests only.

See Also:
    - docs/adr/0033-landing-board-enrolment-two-factor-login-and-exposure.md for the login.
    - tests.e2e.test_hive_serve for a program's side of the same Entrance.
"""

from __future__ import annotations

import asyncio
import os
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
import pytest
from builders.entrance.auth import PASSWORD, WRONG_PASSWORD
from builders.entrance.goals import goal_responder
from builders.entrance.stand import (
    Stand,
    Terminal,
    json_of,
    landing_client,
    laptop_terminal,
    redeem_invite,
    serving_stand,
    set_password,
    stand_manifest,
)
from pydantic import SecretStr
from typer.testing import Result

from hivemind.cli.entrance import read_serve_record
from hivemind.cli.landing import (
    AUTHENTICATION_CODE,
    Credential,
    DeviceKey,
    OutgoingRequest,
    Stamp,
    request_headers,
    signed_in,
)
from waggle.clock import SystemClock
from waggle.signing import Ed25519Signer

pytestmark = pytest.mark.e2e

_QUESTION = "Which season should the haiku be about?"
_STDIN = f"{PASSWORD}\n"
_WRONG_STDIN = f"{WRONG_PASSWORD}\n"
_WAIT_S = 60.0  # The longest this module waits on the Hive: a goal here finishes in seconds.
_LOCKOUT = 5  # [entrance] lockout_attempts' default: five bad passwords lock a device.
# ``hive`` itself as a laptop's shell starts it. The follower is a process of its own because the
# laptop's other terminal (CliRunner) swaps this process's standard streams while it runs.
_HIVE = (sys.executable, "-c", "from hivemind.cli.app import main; main()")


async def test_2_a_laptop_runs_a_goal_and_answers_its_question_through_hive_serve(
    tmp_path: Path,
) -> None:
    path = stand_manifest(tmp_path / "stand")
    assert (await set_password(path)).exit_code == 0
    laptop = laptop_terminal(tmp_path / "laptop")

    async with serving_stand(path, goal_responder(_QUESTION)) as (stand, _entrance):
        device_id = await _enrol(stand, laptop)
        approve = ("approve", device_id, "--spend-cap", "20", "--interactive", "--yes")
        assert (await stand.entrance(*approve)).exit_code == 0
        goal = ("run", "--remote", "write a haiku about bees", "--password-stdin")
        async with _hive_process(laptop, *goal, "--timeout", str(_WAIT_S)) as follower:
            question = await _waiting_question(laptop)
            answer = ("inbox", "--remote", "--password-stdin", "answer", question["id"], "spring")
            answered = await laptop.hive(*answer, stdin=_STDIN)
            # Latency: the goal finishes seconds after the answer; the follower's timeout bounds it.
            output, _ = await asyncio.wait_for(follower.communicate(), _WAIT_S + 10.0)
        tasks = await stand.terminal.hive("tasks", "list", "--manifest", str(path))
        after = await laptop.hive("inbox", "--remote", "--password-stdin", "--json", stdin=_STDIN)

    printed = output.decode("utf-8", errors="replace")
    assert question["text"] == _QUESTION
    assert answered.exit_code == 0 and "answered: task task_" in answered.output
    assert follower.returncode == 0, printed
    assert f"queen asks [{question['id']}]: {_QUESTION}" in printed
    assert "received (RECEIVED)" in printed and " finished at " in printed
    assert "SUCCEEDED" in tasks.output, tasks.output
    # Answered, the question no longer waits (an Alarm might: a Warden's heartbeat late on a busy
    # machine reaches the human too, and is not this test's business).
    assert question["id"] not in [waiting["id"] for waiting in json_of(after)["questions"]]
    assert PASSWORD not in printed and PASSWORD not in answered.output


async def test_2_unauthenticated_pending_locked_and_revoked_requests_are_refused(
    tmp_path: Path,
) -> None:
    path = stand_manifest(tmp_path / "stand")
    await set_password(path)
    laptop = laptop_terminal(tmp_path / "laptop")

    async with serving_stand(path) as (stand, _entrance):
        strangers = await _unauthenticated(stand)
        device_id = await _enrol(stand, laptop)
        pending = await _inbox(laptop)
        await stand.entrance("approve", device_id, "--spend-cap", "5", "--yes")
        approved = await _inbox(laptop)
        wrong = [await _inbox(laptop, _WRONG_STDIN) for _ in range(_LOCKOUT)]
        locked = await _inbox(laptop)
        standing = await _status(stand, device_id)
        unlocked = await stand.entrance("unlock", device_id)
        reopened = await _inbox(laptop)
        revoked = await stand.entrance("revoke", device_id)
        after = await _inbox(laptop)

    assert strangers == {"no credentials": 401, "stolen token": 401, "signed": 200, "replay": 401}
    assert pending.exit_code == 1 and AUTHENTICATION_CODE in pending.output
    assert approved.exit_code == 0, approved.output
    assert [result.exit_code for result in wrong] == [1] * _LOCKOUT
    # Locked: the right password is refused too, until the operator unlocks it on loopback.
    assert locked.exit_code == 1 and AUTHENTICATION_CODE in locked.output
    assert standing == "LOCKED"
    assert unlocked.exit_code == 0 and reopened.exit_code == 0, reopened.output
    assert revoked.exit_code == 0, revoked.output
    assert after.exit_code == 1 and AUTHENTICATION_CODE in after.output
    for result in (pending, *wrong, locked, after):
        assert PASSWORD not in result.output and WRONG_PASSWORD not in result.output


async def _enrol(stand: Stand, laptop: Terminal) -> str:
    """Invite at the Stand, enrol from the laptop's terminal; return the device id."""
    invite = json_of(await stand.entrance("invite", "--device", "laptop", "--json"))
    enrolled = await laptop.hive(
        *("remote", "enrol", invite["url"], "--hive", invite["hive_id"], "--name", "laptop")
    )
    assert enrolled.exit_code == 0, enrolled.output
    return str(invite["device_id"])


async def _inbox(laptop: Terminal, stdin: str = _STDIN) -> Result:
    """``hive inbox --remote`` from the laptop, with ``stdin`` as the password line."""
    return await laptop.hive("inbox", "--remote", "--password-stdin", stdin=stdin)


async def _status(stand: Stand, device_id: str) -> str:
    """The device's status as ``hive entrance devices`` lists it."""
    listed = json_of(await stand.entrance("devices", "--json"))
    return next(str(row["status"]) for row in listed["devices"] if row["id"] == device_id)


async def _waiting_question(laptop: Terminal) -> dict[str, Any]:
    """Read ``hive inbox --remote --json`` until the Drone's question waits; return it."""
    async with asyncio.timeout(_WAIT_S):
        while True:
            listed = await laptop.hive(
                "inbox", "--remote", "--password-stdin", "--json", stdin=_STDIN
            )
            assert listed.exit_code == 0, listed.output
            questions: list[dict[str, Any]] = json_of(listed)["questions"]
            if questions:
                return questions[0]
            await asyncio.sleep(0.2)


@asynccontextmanager
async def _hive_process(laptop: Terminal, *args: str) -> AsyncIterator[asyncio.subprocess.Process]:
    """Run ``hive ARGS`` as the laptop's own process, the operator password on its stdin.

    Killed on the way out if still running, so a failed assertion never leaves it behind.
    """
    env = {**os.environ, **(laptop.env or {})}
    # SAFETY: a fixed argv (this interpreter running hive's entry point) plus the test's own
    # arguments; nothing reaches a shell. Test infrastructure, not hivemind src.
    process = await asyncio.create_subprocess_exec(
        *_HIVE,
        *args,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        env=env,
    )
    try:
        assert process.stdin is not None
        process.stdin.write(_STDIN.encode("utf-8"))
        # Latency: a pipe write of one line to a child that reads it at start-up.
        await asyncio.wait_for(process.stdin.drain(), 10.0)
        process.stdin.close()
        yield process
    finally:
        if process.returncode is None:
            process.kill()
            # Latency: reaping a killed child is immediate.
            await asyncio.wait_for(process.wait(), 10.0)


async def _unauthenticated(stand: Stand) -> dict[str, int]:
    """Send the requests an attacker could: none signed, a stolen token, a replay.

    A program enrols and is approved, and logs in; its session token is then presented with no
    signature at all, signed with another key (the token without the device's key), and as an
    exact replay of a request the device signed. Only the device's own signed request is served.
    """
    invite = json_of(await stand.entrance("invite", "--device", "garden-bot", "--json"))
    program = await redeem_invite(stand, invite["code"], "garden-bot")
    await stand.entrance("approve", program.device_id, "--spend-cap", "5", "--yes")
    record = read_serve_record(stand.db)
    assert record is not None
    request = OutgoingRequest("GET", "/v1/inbox")
    clock = SystemClock()
    async with (
        landing_client(stand) as client,
        signed_in(client, program, SecretStr(PASSWORD)) as board,
        httpx.AsyncClient(base_url=record.origin, trust_env=False, timeout=10.0) as http,
    ):
        credential = board.session.credential
        thief = Credential(credential.token, DeviceKey(program.device_id, Ed25519Signer.generate()))
        signed = request_headers(credential, request, Stamp.now(clock))
        # Latency: each is one loopback round trip, bounded by the client's timeout.
        statuses = {
            "no credentials": (await http.get("/v1/inbox")).status_code,
            "stolen token": (
                await http.get(
                    "/v1/inbox", headers=request_headers(thief, request, Stamp.now(clock))
                )
            ).status_code,
            "signed": (await http.get("/v1/inbox", headers=signed)).status_code,
            "replay": (await http.get("/v1/inbox", headers=signed)).status_code,
        }
    return statuses
