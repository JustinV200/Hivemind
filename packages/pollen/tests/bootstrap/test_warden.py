"""Test pollen.bootstrap.warden: START, WITHDRAW, adoption and the watch over the on-device Warden.

The Warden here is the stub of ``support/runtime_kit.py``: a real background process started from
a finished runtime environment written into the data directory, which records the ``HIVEMIND_*``
variables it was given and leaves a marker when it is asked to stop. What is under test is the
packet's side: that it starts one process and records it with the level, never a second, stops it
the way it was asked, finds it again after a restart and does not claim one that is gone.

Fits into the Hive:
    Mirrors src/pollen/bootstrap/warden.py (codingrules section 3).

Key invariants:
    - Every test that starts a Warden ends it before it returns (the ``keeper`` fixture), so no
      process outlives a test.

See Also:
    - pollen.bootstrap.warden for the module under test.
    - hivemind's tests/contracts/test_pollen_warden_env.py for the other end of the environment.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import os
import signal
import stat
import sys
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from executor_kit import Kit, make_kit
from runtime_kit import STUB_VERSION, fake_runtime, wait_for_file

from pollen.bootstrap import BootstrapTimings, WardenKeeper, warden_environment
from pollen.errors import ColonizeFailedError, StateError
from pollen.platform.base import PlatformShim, ProcessRef, stop_group
from pollen.state import InstallSettings, PacketState, StateStore, WardenRecord
from waggle.clock import Clock
from waggle.messages.swarm import RuntimeLevel

pytestmark = pytest.mark.skipif(
    sys.platform == "win32", reason="the stub Warden is a POSIX script that handles SIGTERM"
)

URL = "ws://127.0.0.1:8720"
FAST = BootstrapTimings(settle_s=0.5, stop_grace_s=3.0, watch_interval_s=0.05)


def _prepared(tmp_path: Path, clock: Clock, **state: object) -> Kit:
    """A device with a finished runtime and a device manifest recorded."""
    manifest = tmp_path / "warden.toml"
    manifest.write_text("[llm]\n", encoding="utf-8")
    state.setdefault("install", InstallSettings(warden_manifest=str(manifest)))
    kit = make_kit(tmp_path, clock, **state)
    fake_runtime(kit.layout, kit.deps.shim)
    return kit


def _env(kit: Kit) -> dict[str, str]:
    """The variables the stub Warden recorded."""
    text = (kit.layout.warden_state / "env.json").read_text(encoding="utf-8")
    recorded: dict[str, str] = json.loads(text)
    return recorded


def _restarted(kit: Kit) -> WardenKeeper:
    """A keeper as a restarted packet builds it: the state read back from disk, no child handle."""
    deps = dataclasses.replace(kit.deps, state=StateStore.load(kit.layout.state))
    return WardenKeeper(deps, FAST)


async def _wait_gone(kit: Kit, record: WardenRecord) -> None:
    """Wait until the Warden's process group has gone, a few times a second."""
    ref = ProcessRef(record.pid, record.created)
    for _ in range(200):
        if not kit.deps.shim.group_alive(ref):
            return
        await asyncio.sleep(0.02)
    raise AssertionError("The Warden is still running.")


@pytest.fixture
async def keeper(tmp_path: Path, clock: Clock) -> AsyncIterator[tuple[WardenKeeper, Kit]]:
    """A keeper over a prepared device; any Warden it started is ended when the test is."""
    kit = _prepared(tmp_path, clock)
    held = WardenKeeper(kit.deps, FAST)
    try:
        yield held, kit
    finally:
        record = kit.deps.state.current.warden
        if record is not None:
            os.killpg(record.pid, signal.SIGKILL)
        await asyncio.sleep(0.1)  # Let the child watcher collect the process.


async def test_start_runs_the_warden_with_the_environment_the_runtime_reads(
    keeper: tuple[WardenKeeper, Kit],
) -> None:
    held, kit = keeper

    record = await held.start(URL)
    await wait_for_file(kit.layout.warden_state / "ready")

    state = kit.deps.state.current
    assert (state.level, state.warden) == (RuntimeLevel.COLONIZED, record)
    assert record.runtime_version == STUB_VERSION
    env = _env(kit)
    assert env["HIVEMIND_CELL_LEASE"] == "borrowed"
    assert env["HIVEMIND_QUEEN_WAGGLE_URL"] == URL
    assert (env["HIVEMIND_CELL_ID"], env["HIVEMIND_WARDEN_ID"]) == (state.cell_id, state.warden_id)
    assert (env["HIVEMIND_NODE_ID"], env["HIVEMIND_HIVE_ID"]) == (state.node_id, state.hive_id)
    assert env["HIVEMIND_QUEEN_NODE_ID"] == state.hive_node_id
    assert env["HIVEMIND_QUEEN_VERIFY_KEY"] == state.hive_key_hex
    assert env["HIVEMIND_CELL_SIGNING_KEY_FILE"] == str(kit.layout.key)  # The packet's own key.
    assert env["HIVEMIND_STATE_DIR"] == str(kit.layout.warden_state)
    assert env["HIVEMIND_ACCESS_LEVEL"] == state.access_level.value
    assert env["HIVEMIND_COMB_SHIELD"] == state.comb_shield.value
    assert env["HIVEMIND_DEVICE_MANIFEST"] == state.install.warden_manifest
    assert "HIVEMIND_TLS_PIN" not in env  # A ws:// listener has none.
    assert kit.layout.warden_log.is_file()


async def test_a_wss_listener_is_given_the_pin_the_packet_trusts(
    tmp_path: Path, clock: Clock
) -> None:
    pin = "ab" * 32
    kit = _prepared(tmp_path, clock, queen_url="wss://hive.example.org:8443", tls_pin_sha256=pin)
    held = WardenKeeper(kit.deps, FAST)

    record = await held.start("wss://hive.example.org:8443")
    try:
        await wait_for_file(kit.layout.warden_state / "ready")
        assert _env(kit)["HIVEMIND_TLS_PIN"] == pin
    finally:
        os.killpg(record.pid, signal.SIGKILL)
        await asyncio.sleep(0.1)


async def test_starting_again_finds_the_running_warden_and_starts_no_second(
    keeper: tuple[WardenKeeper, Kit],
) -> None:
    held, _kit = keeper

    first = await held.start(URL)
    second = await held.start(URL)

    assert second == first


async def test_a_restarted_packet_adopts_the_warden_it_finds_alive(
    keeper: tuple[WardenKeeper, Kit],
) -> None:
    held, kit = keeper
    record = await held.start(URL)

    # A new packet process: its state is read from disk and it holds no handle on the child.
    restarted = _restarted(kit)

    assert restarted.running() == record
    assert await restarted.start(URL) == record  # Never a second Warden.
    await restarted.reconcile()
    assert kit.deps.state.current.level is RuntimeLevel.COLONIZED


async def test_withdraw_asks_the_warden_to_stop_and_returns_the_device_to_gateway(
    keeper: tuple[WardenKeeper, Kit],
) -> None:
    held, kit = keeper
    record = await held.start(URL)
    await wait_for_file(kit.layout.warden_state / "ready")

    await held.withdraw()

    state = kit.deps.state.current
    assert (state.level, state.warden) == (RuntimeLevel.GATEWAY, None)
    assert (kit.layout.warden_state / "stopped").read_text() == "term"  # It was asked, not killed.
    await _wait_gone(kit, record)
    await held.withdraw()  # Repeating it is the same answer.


async def test_withdraw_kills_a_warden_that_ignores_the_request_after_the_grace(
    tmp_path: Path, clock: Clock, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HIVEMIND_STUB_MODE", "ignore-term")
    kit = _prepared(tmp_path, clock)
    held = WardenKeeper(kit.deps, BootstrapTimings(settle_s=0.5, stop_grace_s=0.3))
    record = await held.start(URL)
    await wait_for_file(kit.layout.warden_state / "ready")

    await held.withdraw()

    assert kit.deps.state.current.warden is None
    await _wait_gone(kit, record)
    assert not (kit.layout.warden_state / "stopped").exists()  # It never got to say goodbye.


async def test_a_warden_that_survives_the_kill_stays_recorded_and_the_failure_says_so(
    keeper: tuple[WardenKeeper, Kit], monkeypatch: pytest.MonkeyPatch
) -> None:
    held, kit = keeper
    record = await held.start(URL)

    async def survives(*_: object) -> bool:
        return False  # The group is still there after the kill.

    monkeypatch.setattr("pollen.bootstrap.warden.stop_recorded", survives)
    with pytest.raises(ColonizeFailedError, match="could not be stopped"):
        await held.withdraw()

    # The Warden is still there, so the state still says so.
    state = kit.deps.state.current
    assert (state.level, state.warden) == (RuntimeLevel.COLONIZED, record)


async def test_a_start_whose_record_cannot_be_written_leaves_no_warden_running(
    tmp_path: Path, clock: Clock, monkeypatch: pytest.MonkeyPatch
) -> None:
    kit = _prepared(tmp_path, clock)
    held = WardenKeeper(kit.deps, FAST)
    stopped: list[ProcessRef] = []

    async def failing_update(*_: object, **__: object) -> PacketState:
        raise StateError("The disk is full.")

    async def recording_stop(
        shim: PlatformShim, ref: ProcessRef, *, graceful: bool, clock: Clock
    ) -> bool:
        stopped.append(ref)
        return await stop_group(shim, ref, graceful=graceful, clock=clock)

    monkeypatch.setattr(StateStore, "update", failing_update)
    monkeypatch.setattr("pollen.bootstrap.warden.stop_group", recording_stop)

    with pytest.raises(StateError):
        await held.start(URL)

    # A Warden nobody could find again was started and is ended before the failure is reported.
    assert len(stopped) == 1 and not kit.deps.shim.group_alive(stopped[0])
    assert kit.deps.state.current.warden is None


async def test_the_wardens_output_goes_to_a_private_log_and_the_last_runs_is_kept(
    keeper: tuple[WardenKeeper, Kit],
) -> None:
    held, kit = keeper
    await held.start(URL)
    await wait_for_file(kit.layout.warden_state / "ready")
    await held.withdraw()
    (kit.layout.warden_state / "ready").unlink()

    await held.start(URL)  # The second run rotates the first one's log aside.
    await wait_for_file(kit.layout.warden_state / "ready")

    log, previous = kit.layout.warden_log, kit.layout.warden_log.with_name("warden.log.1")
    assert (
        "stub warden started" in log.read_text() and "stub warden started" in previous.read_text()
    )
    assert stat.S_IMODE(log.stat().st_mode) == 0o600  # What a Warden says is the owner's alone.


async def test_a_warden_that_dies_at_once_is_reverted_before_it_is_reported(
    tmp_path: Path, clock: Clock, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HIVEMIND_STUB_MODE", "exit")
    kit = _prepared(tmp_path, clock)
    held = WardenKeeper(kit.deps, FAST)

    with pytest.raises(ColonizeFailedError, match="exited with status 3") as raised:
        await held.start(URL)

    state = kit.deps.state.current
    assert (state.level, state.warden) == (RuntimeLevel.GATEWAY, None)
    assert str(tmp_path) not in str(raised.value)  # No path in what the Queen is told.


async def test_start_refuses_without_a_prepared_runtime(tmp_path: Path, clock: Clock) -> None:
    kit = _prepared(tmp_path, clock)
    for program in kit.layout.venv.rglob("*"):
        if program.is_file():
            program.unlink()

    with pytest.raises(ColonizeFailedError, match="has not been prepared"):
        await WardenKeeper(kit.deps, FAST).start(URL)


async def test_start_refuses_without_a_recorded_device_manifest(
    tmp_path: Path, clock: Clock
) -> None:
    kit = _prepared(tmp_path, clock, install=InstallSettings())

    with pytest.raises(ColonizeFailedError, match="--warden-manifest"):
        await WardenKeeper(kit.deps, FAST).start(URL)


async def test_start_refuses_a_wss_listener_the_packet_holds_no_pin_for(
    tmp_path: Path, clock: Clock
) -> None:
    kit = _prepared(tmp_path, clock)  # Its own listener is ws://: no pin.

    with pytest.raises(ColonizeFailedError, match="no certificate pin"):
        await WardenKeeper(kit.deps, FAST).start("wss://hive.example.org:8443")


async def test_start_refuses_a_runtime_of_another_version_than_the_order_pins(
    tmp_path: Path, clock: Clock
) -> None:
    kit = _prepared(tmp_path, clock)

    with pytest.raises(ColonizeFailedError, match="not the version the order pins"):
        await WardenKeeper(kit.deps, FAST).start(URL, pinned="9.9.9")


async def test_reconcile_forgets_a_warden_that_died_so_the_dead_man_switch_resumes(
    keeper: tuple[WardenKeeper, Kit],
) -> None:
    held, kit = keeper
    record = await held.start(URL)
    await wait_for_file(kit.layout.warden_state / "ready")
    os.killpg(record.pid, signal.SIGKILL)  # It dies on its own, not by WITHDRAW.
    await _wait_gone(kit, record)

    await held.reconcile()

    state = kit.deps.state.current
    assert (state.level, state.warden) == (RuntimeLevel.GATEWAY, None)


async def test_a_recycled_pid_is_not_taken_for_the_warden(tmp_path: Path, clock: Clock) -> None:
    kit = _prepared(tmp_path, clock)
    impostor = WardenRecord(
        pid=os.getpid(), created=1.0, runtime_version=STUB_VERSION, started_at=clock.now()
    )  # A live process, but not the one that was recorded: it was created long before.
    await kit.deps.state.update(level=RuntimeLevel.COLONIZED, warden=impostor)
    held = WardenKeeper(kit.deps, FAST)

    assert held.running() is None
    await held.reconcile()

    assert kit.deps.state.current.warden is None


async def test_the_watch_notices_a_death_while_the_packet_runs(
    keeper: tuple[WardenKeeper, Kit],
) -> None:
    held, kit = keeper
    record = await held.start(URL)
    watching = asyncio.ensure_future(held.watch())
    try:
        os.killpg(record.pid, signal.SIGKILL)
        for _ in range(200):
            if kit.deps.state.current.warden is None:
                break
            await asyncio.sleep(0.02)
    finally:
        watching.cancel()
        await asyncio.gather(watching, return_exceptions=True)

    assert kit.deps.state.current.level is RuntimeLevel.GATEWAY


def test_the_environment_names_are_the_ones_the_runtime_reads(tmp_path: Path, clock: Clock) -> None:
    kit = make_kit(tmp_path, clock)
    state = kit.deps.state.current

    env = warden_environment(state, Path("/k"), Path("/s"), URL, Path("/m"))

    assert set(env) == {
        "HIVEMIND_CELL_LEASE",
        "HIVEMIND_QUEEN_WAGGLE_URL",
        "HIVEMIND_CELL_ID",
        "HIVEMIND_WARDEN_ID",
        "HIVEMIND_NODE_ID",
        "HIVEMIND_HIVE_ID",
        "HIVEMIND_QUEEN_NODE_ID",
        "HIVEMIND_QUEEN_VERIFY_KEY",
        "HIVEMIND_CELL_SIGNING_KEY_FILE",
        "HIVEMIND_ACCESS_LEVEL",
        "HIVEMIND_COMB_SHIELD",
        "HIVEMIND_STATE_DIR",
        "HIVEMIND_DEVICE_MANIFEST",
    }
