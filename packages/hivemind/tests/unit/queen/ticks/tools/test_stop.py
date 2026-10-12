"""Tests for the tool lane's end of life: `Queen.stop` with tool jobs in flight, and the jobs.

Fits into the Hive:
    Mirrors src/hivemind/queen/ticks/tools/jobs.py and the stop path of src/hivemind/queen/
    queen.py (codingrules section 3). A real `Queen` is built over the rig's deps; a request is
    started so its job is waiting on a sandbox, and the Queen is stopped under it.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.queen.ticks.tools.jobs for the module under test.
    - builders.queen_tools for the rig.
"""

from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path

import pytest
from builders.basket import make_deposit_request
from builders.queen_tools import FakeSandboxProvider, ToolRig, WardenScript, make_tool_rig

from hivemind.manifest import SandboxKind
from hivemind.pheromone.trail import TrailQuery
from hivemind.queen.attach import detach_warden
from hivemind.queen.queen import Queen
from hivemind.queen.ticks.tools import replies
from hivemind.queen.ticks.tools.jobs import start_background, start_job, stop_tools
from hivemind.queen.ticks.tools.transfer import ShipResult, ship
from waggle.messages.control.protocol import ErrorMessage


async def _waiting_for_the_sandbox(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provider: FakeSandboxProvider
) -> tuple[Queen, ToolRig]:
    rig = await make_tool_rig(
        tmp_path,
        monkeypatch,
        sandbox=SandboxKind.VIRTUAL,
        provider=provider,
        request_timeout_s=3600.0,
    )
    queen = Queen(rig.deps)
    await rig.send(rig.request())
    return queen, rig


async def test_stopping_the_queen_cancels_a_job_waiting_on_a_sandbox_and_releases_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider = FakeSandboxProvider(WardenScript(silent=True))  # The report never comes.
    queen, rig = await _waiting_for_the_sandbox(tmp_path, monkeypatch, provider)
    await asyncio.wait_for(provider.ordered.wait(), timeout=10)  # Shipped, ordered: the job waits.
    (job,) = rig.deps.tool_lane.jobs.values()

    await asyncio.wait_for(queen.stop(), timeout=10)

    assert job.cancelled()
    assert provider.released == [provider.sandbox_wardens[0].link.cell.id]
    events = await rig.deps.trail.query(TrailQuery(kind="tool.sandbox_released"))
    assert len(events) == 1
    lane = rig.deps.tool_lane
    assert lane.jobs == {} and lane.pending_reports == {} and lane.sandbox_cells == set()
    await rig.aclose()


async def test_stopping_the_queen_lets_a_sandbox_that_is_being_made_finish_then_releases_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider = FakeSandboxProvider()
    provider.gate = asyncio.Event()  # The provision is under way and cannot finish yet.
    queen, rig = await _waiting_for_the_sandbox(tmp_path, monkeypatch, provider)
    await asyncio.wait_for(provider.started.wait(), timeout=10)

    stopping = asyncio.ensure_future(queen.stop())
    await asyncio.sleep(0.1)
    assert not stopping.done()  # A provision is awaited, never cut short (it could orphan a Cell).
    provider.gate.set()
    await asyncio.wait_for(stopping, timeout=10)

    (warden,) = provider.sandbox_wardens
    assert provider.released == [warden.link.cell.id]
    await rig.aclose()


async def test_a_request_after_the_queen_stopped_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = await make_tool_rig(tmp_path, monkeypatch)
    await stop_tools(rig.deps)

    await rig.send(rig.request())

    error = (await rig.reply()).payload
    assert isinstance(error, ErrorMessage) and error.code == replies.REQUEST_REFUSED
    await rig.aclose()


async def test_a_job_that_will_never_run_is_closed_not_leaked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = await make_tool_rig(tmp_path, monkeypatch)
    lane = rig.deps.tool_lane
    lane.closed = True
    ran: list[str] = []

    async def work() -> None:
        ran.append("ran")

    assert start_job(rig.deps, rig.request().tool_id, work()) is False
    assert start_background(rig.deps, work()) is False
    await asyncio.sleep(0)

    assert ran == [] and lane.jobs == {} and lane.background == set()
    await rig.aclose()


async def test_a_finished_job_wakes_the_tick_and_leaves_the_lane() -> None:
    from builders.queen import make_queen_deps

    deps, _link, _end = make_queen_deps()
    deps.wake.clear()

    async def work() -> None:
        return None

    assert start_background(deps, work()) is True
    await asyncio.wait_for(deps.wake.wait(), timeout=5)
    await asyncio.sleep(0)
    assert deps.tool_lane.background == set()


async def test_a_warden_that_detaches_ends_a_shipment_waiting_on_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = await make_tool_rig(tmp_path, monkeypatch, script=WardenScript(answers=False))
    await rig.access.deposits.deposit(make_deposit_request(b"a blob for a Warden that will leave"))
    digest = hashlib.sha256(b"a blob for a Warden that will leave").hexdigest()
    queen = Queen(rig.deps)
    await queen.attach_warden(rig.link)
    shipping = asyncio.ensure_future(ship(rig.deps, rig.link, digest, "a test shipment"))
    await rig.peer.next()  # The offer is out and nobody will ever answer it.

    await detach_warden(queen, rig.link.warden_id)

    assert await asyncio.wait_for(shipping, timeout=5) is ShipResult.FAILED
    assert rig.deps.tool_lane.transfers == {}
    await queen.stop()
    await rig.aclose()


async def test_a_sandbox_warden_that_detaches_fails_the_run_at_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider = FakeSandboxProvider(WardenScript(silent=True))
    queen, rig = await _waiting_for_the_sandbox(tmp_path, monkeypatch, provider)
    await asyncio.wait_for(provider.ordered.wait(), timeout=10)
    (sandbox,) = provider.sandbox_wardens

    await detach_warden(queen, sandbox.link.warden_id)
    await asyncio.wait_for(rig.settle(), timeout=10)  # Long before the one-hour request timeout.

    error = (await rig.reply()).payload
    assert isinstance(error, ErrorMessage) and error.code == replies.SANDBOX_UNAVAILABLE
    assert provider.released == [sandbox.link.cell.id]
    await queen.stop()
    await rig.aclose()
