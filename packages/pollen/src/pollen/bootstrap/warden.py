"""Start, stop and keep track of the Warden that lives on the device (START and WITHDRAW).

Once PREPARE has installed the runtime, START launches ``<venv>/bin/hivemind-in-cell`` as a
long-lived background process, the device's own Warden (Level 1 of the runtime ladder, ADR-0052). It
is started in a process group of its own and is not part of any lease: it outlives requests, and a
lease's release must never kill it. Everything it needs it is given through its environment
(``warden_environment``): the swarm listener's URL and certificate pin, its Warden id and Cell id,
the device's node id and the file holding its key (the packet's own, so the Warden signs as the
device), the Queen's address and key, the access level and tier, the directories the operator named
for it to watch, the directory it keeps its stores in and the device manifest the operator recorded.
Terms are fixed at start: a Warden that is to run under others is stopped and started again (the
Queen's terms renewal), which builds this environment afresh from the packet's state. The packet
records the process (pid and creation time) in its state together with the new level, COLONIZED, in
one write, so the dead-man switch, which stands down while a Warden lives on the device, and the
record that says one does can never disagree; a packet that is restarted finds the record and adopts
the Warden if it is still alive, instead of starting a second. WITHDRAW asks the Warden to stop
(TERM, which it turns into releasing its lease and leaving the device as found), waits a named
grace, then kills the group, and puts the level back to GATEWAY. ``WardenKeeper.watch`` notices a
Warden that has died on its own and does the same, so the packet never claims a Warden it does not
have.

Fits into the Hive:
    Parallel to hivemind's layer table, inside the pollen package. Called by
    ``pollen.bootstrap.service`` for START and WITHDRAW and run as a task of the packet's scope
    for the life of the packet; calls into the platform shim, ``pollen.state``,
    ``pollen.bootstrap.runtime`` and waggle only. The names it sets in the Warden's environment
    are a contract with ``hivemind.manifest.env`` (checked by a test in hivemind), not an import.

Key invariants:
    - At most one Warden is recorded and running: START finds a live one and returns it, and a
      recorded process whose pid has been recycled (the creation time differs) is not a Warden.
    - The level and the Warden record are written together or not at all; a start that does not
      survive its first moments is reverted before it is reported.
    - Nothing the Warden needs comes from the wire except the listener URL, which is checked by
      ``waggle.uris`` first and is useless without the pin of the listener the packet already
      trusts; the key never leaves the file the packet keeps it in.
    - A failure names no path, output or argument; the Warden's own log is on the device.

See Also:
    - docs/adr/0052-runtime-ladder-warden-moves-first.md for the three actions.
    - hivemind.cli.in_cell.borrowed.README for the environment's other end.
    - pollen.bootstrap.runtime for PREPARE, whose entry point this starts.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import IO

from pollen.bootstrap.runtime import REAP_WAIT_S, entry_point, installed_version
from pollen.errors import ColonizeFailedError
from pollen.executors.base import ExecutorDeps
from pollen.platform.base import (
    DISPLAY_VARIABLES,
    PlatformShim,
    ProcessRef,
    SpawnSpec,
    child_environment,
    stop_group,
)
from pollen.state import PRIVATE_DIR_MODE, PRIVATE_FILE_MODE, PacketState, WardenRecord
from waggle.clock import Clock
from waggle.messages.swarm import RuntimeLevel

log = logging.getLogger(__name__)

SETTLE_S = 2.0  # A start that has not died within this long is reported as started.
STOP_GRACE_S = (
    30.0  # Longer than the Warden's own shutdown grace (hivemind: 20 s), so it can finish.
)
WATCH_INTERVAL_S = 10.0  # How often the packet looks to see whether its Warden is still there.

__all__ = [
    "SETTLE_S",
    "STOP_GRACE_S",
    "WATCH_INTERVAL_S",
    "BootstrapTimings",
    "WardenKeeper",
    "operator_screen",
    "stop_recorded",
    "warden_environment",
]


@dataclass(frozen=True, slots=True)
class BootstrapTimings:
    """The three waits of the Warden's keeper; a test shortens them."""

    settle_s: float = SETTLE_S  # How long a fresh start must stay alive to be reported.
    stop_grace_s: float = STOP_GRACE_S  # How long WITHDRAW waits after TERM before it kills.
    watch_interval_s: float = WATCH_INTERVAL_S  # Between looks at whether the Warden lives.


def warden_environment(
    state: PacketState, key_file: Path, state_dir: Path, url: str, manifest: Path
) -> dict[str, str]:
    """Return the variables the on-device Warden is started with (overlaid on the packet's own).

    Args:
        state: The packet's persisted identity, pins and grants.
        key_file: The file holding the device's node key: the packet's own.
        state_dir: The directory the Warden keeps its stores, outbox and scratch in.
        url: The swarm listener's URL the Queen's order named.
        manifest: The device manifest the operator recorded at install.

    Returns:
        The variables ``hivemind.manifest.env.read_in_cell_env`` reads for a borrowed Warden.
    """
    env = {
        "HIVEMIND_CELL_LEASE": "borrowed",
        "HIVEMIND_QUEEN_WAGGLE_URL": url,
        "HIVEMIND_CELL_ID": state.cell_id,
        "HIVEMIND_WARDEN_ID": state.warden_id,
        "HIVEMIND_NODE_ID": state.node_id,
        "HIVEMIND_HIVE_ID": state.hive_id,
        "HIVEMIND_QUEEN_NODE_ID": state.hive_node_id,
        "HIVEMIND_QUEEN_VERIFY_KEY": state.hive_key_hex,
        "HIVEMIND_CELL_SIGNING_KEY_FILE": str(key_file),
        "HIVEMIND_ACCESS_LEVEL": state.access_level.value,
        "HIVEMIND_COMB_SHIELD": state.comb_shield.value,
        "HIVEMIND_STATE_DIR": str(state_dir),
        "HIVEMIND_DEVICE_MANIFEST": str(manifest),
    }
    # The pin belongs to a wss:// listener; a ws:// loopback one has none (the Warden refuses both
    # a missing and a surplus pin, as the packet's own state does).
    if url.startswith("wss://") and state.tls_pin_sha256 is not None:
        env["HIVEMIND_TLS_PIN"] = state.tls_pin_sha256
    # The directories the operator named for the Warden to watch, as the Queen last told the packet
    # (``swarm.node_update``): a JSON array, which any path survives, set only when there are some
    # so that a device with none named starts its Warden exactly as before.
    if state.watch_roots:
        env["HIVEMIND_WATCH_ROOTS"] = json.dumps(list(state.watch_roots))
    return env


def operator_screen(manifest: Path, env: Mapping[str, str]) -> dict[str, str]:
    """Return the packet's DISPLAY and WAYLAND_DISPLAY if the device manifest lends the screen.

    Every program the packet starts loses these (``child_environment``), so nothing it runs can
    appear on the operator's screen unless the operator asked: here, by setting ``[hive_stand]
    exoskeleton_real_display = true`` in the manifest recorded at install.

    Args:
        manifest: The device manifest the operator recorded.
        env: The packet's own environment, which holds the screen's variables if any.

    Returns:
        The screen's variables to give the Warden back; empty when the manifest does not lend
        it or cannot be read (the Warden then reports the manifest itself).
    """
    try:
        document = tomllib.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError):
        return {}
    section = document.get("hive_stand")
    # Only a literal true lends it: the operator's screen is never driven by a mistyped value.
    if not isinstance(section, dict) or section.get("exoskeleton_real_display") is not True:
        return {}
    return {name: env[name] for name in DISPLAY_VARIABLES if name in env}


class WardenKeeper:
    """Owns the on-device Warden's process: starts it, withdraws it and notices its death.

    Owns mutable state (codingrules 8.5, documented): ``_process`` is the handle of the Warden
    this packet instance started, kept so the child is reaped; a Warden adopted after a restart has
    none and is tracked by its recorded pid alone.
    """

    def __init__(self, deps: ExecutorDeps, timings: BootstrapTimings | None = None) -> None:
        """Bind the keeper to the packet's collaborators.

        Args:
            deps: State, platform shim, data layout and clock.
            timings: The settle, stop grace and watch interval; the defaults when None.
        """
        self._deps = deps
        self._timings = timings if timings is not None else BootstrapTimings()
        self._process: asyncio.subprocess.Process | None = None

    def running(self) -> WardenRecord | None:
        """Return the recorded Warden if its process is alive, else None."""
        record = self._deps.state.current.warden
        return record if record is not None and self._alive(record) else None

    async def start(self, url: str, pinned: str | None = None) -> WardenRecord:
        """Start the Warden against ``url``, or return the one that is already running.

        Args:
            url: The swarm listener's URL from the order; the caller has checked it.
            pinned: The runtime version the order pins to start, or None for whatever is prepared.

        Returns:
            The recorded Warden.

        Raises:
            ColonizeFailedError: The runtime is not prepared, no device manifest was recorded, the
                listener needs a pin the packet does not hold, the program could not be started,
                or the Warden died within the settle time.
        """
        running = self.running()
        if running is not None:
            _check_pin(running.runtime_version, pinned)
            return running  # A restarted packet, or a repeated order: never a second Warden.
        deps = self._deps
        state = deps.state.current
        version = await installed_version(deps)
        if version is None:
            raise ColonizeFailedError("The runtime has not been prepared on this device.")
        _check_pin(version, pinned)
        manifest = self._manifest(state)
        if url.startswith("wss://") and state.tls_pin_sha256 is None:
            raise ColonizeFailedError("The packet holds no certificate pin for a wss:// listener.")
        env = warden_environment(state, deps.layout.key, deps.layout.warden_state, url, manifest)
        env.update(operator_screen(manifest, os.environ))
        record = await self._spawn(env, version)
        await self._settle(record)
        return record

    async def withdraw(self) -> None:
        """Stop the Warden (TERM, then kill after the grace) and put the level back to GATEWAY.

        Raises:
            ColonizeFailedError: The Warden survived the kill; the record stays, since it is
                still there.
        """
        record = self._deps.state.current.warden
        if record is None:
            return  # Nothing to withdraw: the answer to a repeated order is the same.
        gone = await stop_recorded(
            record, self._deps.shim, self._deps.clock, self._timings.stop_grace_s
        )
        await self._reap()
        if not gone:
            raise ColonizeFailedError("The Warden could not be stopped.")
        await self._forget()

    async def reconcile(self) -> None:
        """Forget a recorded Warden whose process is gone, so the packet stops claiming it."""
        record = self._deps.state.current.warden
        if record is not None and not self._alive(record):
            log.warning("pollen.warden_gone pid=%d", record.pid)
            await self._forget()

    async def watch(self) -> None:
        """Look at the Warden every ``watch_interval_s`` for as long as the packet runs."""
        while True:
            await self.reconcile()
            await self._deps.clock.sleep(self._timings.watch_interval_s)

    # ──────────────────────────────────────────────────────────────────────────
    # Inside a start
    # ──────────────────────────────────────────────────────────────────────────

    def _manifest(self, state: PacketState) -> Path:
        """Return the recorded device manifest, or raise why there is none to give the Warden."""
        recorded = state.install.warden_manifest
        if recorded is None or not Path(recorded).is_file():
            raise ColonizeFailedError(
                "No device manifest was recorded; run pollen install with --warden-manifest."
            )
        return Path(recorded)

    async def _spawn(self, env: dict[str, str], version: str) -> WardenRecord:
        """Start the program and record it with the new level, in one write."""
        deps = self._deps
        await asyncio.to_thread(_prepare_directories, deps.layout.warden_state, deps.layout.runtime)
        log_file = await asyncio.to_thread(_open_log, deps.layout.warden_log)
        try:
            process = await deps.shim.spawn(
                SpawnSpec(
                    argv=(str(entry_point(deps)),),
                    cwd=deps.layout.runtime,
                    env={**child_environment(os.environ), **env},
                    stdin=asyncio.subprocess.DEVNULL,
                    stdout=log_file,
                    stderr=asyncio.subprocess.STDOUT,
                    # In the packet's console, so a break asks it to hand its leases back.
                    shares_console=True,
                )
            )
        except OSError:
            raise ColonizeFailedError("The Warden program could not be started.") from None
        finally:
            log_file.close()  # The child holds its own copy of the descriptor.
        self._process = process
        ref = deps.shim.process_ref(process.pid)
        record = WardenRecord(
            pid=process.pid,
            created=ref.created,
            runtime_version=version,
            started_at=deps.clock.now(),
        )
        try:
            await deps.state.update(level=RuntimeLevel.COLONIZED, warden=record)
        except BaseException:
            # Recorded or not, a Warden nobody can find again must not be left running.
            await stop_group(deps.shim, ref, graceful=False, clock=deps.clock)
            raise
        return record

    async def _settle(self, record: WardenRecord) -> None:
        """Give the new Warden a moment to die of a setup mistake; undo the start if it does."""
        process = self._process
        if process is None:
            return
        try:
            # Local wait on our own child, bounded by the settle time; reaching the end of it
            # alive is the success case.
            async with asyncio.timeout(self._timings.settle_s):
                code = await process.wait()
        except TimeoutError:
            return
        await self._forget()
        raise ColonizeFailedError(
            f"The Warden exited with status {code} right after it started; its log is on the "
            f"device (pid {record.pid})."
        )

    # ──────────────────────────────────────────────────────────────────────────
    # Looking at the process
    # ──────────────────────────────────────────────────────────────────────────

    def _alive(self, record: WardenRecord) -> bool:
        """Say whether the recorded process, or its group, is still running."""
        return self._deps.shim.group_alive(ProcessRef(record.pid, record.created))

    async def _reap(self) -> None:
        """Collect the child this instance started, if it has ended, so it is not left a zombie."""
        process = self._process
        if process is None:
            return
        with contextlib.suppress(TimeoutError):
            async with asyncio.timeout(REAP_WAIT_S):
                await process.wait()
        if process.returncode is not None:
            self._process = None

    async def _forget(self) -> None:
        """Write the level back to GATEWAY with no Warden recorded."""
        await self._deps.state.update(level=RuntimeLevel.GATEWAY, warden=None)
        self._process = None


async def stop_recorded(
    record: WardenRecord, shim: PlatformShim, clock: Clock, grace_s: float = STOP_GRACE_S
) -> bool:
    """Stop the Warden ``record`` names: ask it to (TERM), wait ``grace_s``, then kill its group.

    Args:
        record: The recorded Warden; a process that is not the one recorded (its pid was
            recycled) is not signalled.
        shim: The platform shim that signals the group.
        clock: Sleeps between looks at the group.
        grace_s: How long the Warden may take to leave after it is asked.

    Returns:
        True when the Warden is gone, whether or not it was running when asked.
    """
    ref = ProcessRef(record.pid, record.created)
    await stop_group(shim, ref, graceful=True, clock=clock, grace_s=grace_s)
    return not shim.group_alive(ref)


def _check_pin(version: str, pinned: str | None) -> None:
    """Refuse to start a runtime of another version than the order pins."""
    if pinned is not None and version != pinned:
        raise ColonizeFailedError("The runtime on the device is not the version the order pins.")


def _prepare_directories(state_dir: Path, runtime: Path) -> None:
    """Create the runtime directory and the Warden's state directory, owner-only."""
    runtime.mkdir(mode=PRIVATE_DIR_MODE, parents=True, exist_ok=True)
    state_dir.mkdir(mode=PRIVATE_DIR_MODE, parents=True, exist_ok=True)


def _open_log(path: Path) -> IO[bytes]:
    """Open the Warden's log for appending, owner-only, keeping the previous run's beside it."""
    if path.is_file() and path.stat().st_size > 0:
        os.replace(path, path.with_name(path.name + ".1"))
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, PRIVATE_FILE_MODE)
    return os.fdopen(descriptor, "ab")
