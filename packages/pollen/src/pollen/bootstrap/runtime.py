"""Install the pinned hivemind runtime into a virtual environment the packet owns (PREPARE).

Colonization (ADR-0052) moves the device's Warden onto the device, and the Warden is the
``hivemind`` Python package. The Pollen Packet (the thin gateway program on a device the Hive
borrows) cannot carry it: a frozen single-file packet has no interpreter to run it with, and the
packet may import nothing from ``hivemind`` anyway. So the first step, PREPARE, builds the runtime
beside the packet. It finds a Python 3.12 or newer on the device (the one ``pollen install
--runtime-python`` recorded, else the platform shim's search: ``python3.13``, ``python3.12``,
``python3``, then the interpreter running the packet unless that is a frozen binary), makes a
virtual environment under the data directory, installs ``hivemind==<version>`` into it from the
wheelhouse or the package index the operator recorded at install time and from nowhere else, and
checks that the version installed is the one asked for and that its Warden entry point exists.
The version comes from the Queen's order but never the source: nothing on the wire says where to
fetch code from. Every external program runs in a process group of its own with the packet's
interpreter variables removed, under a timeout, and is ended whole if the step is cancelled, so a
deadline that passes does not leave a pip running.

Fits into the Hive:
    Parallel to hivemind's layer table, inside the pollen package. Called by
    ``pollen.bootstrap.service`` for a ``swarm.colonize`` PREPARE and by ``pollen.bootstrap.warden``
    to find the entry point; calls into the platform shim, ``pollen.state`` and the standard
    library only.

Key invariants:
    - Code is installed only from the source the operator recorded (``--runtime-wheelhouse`` or
      ``--runtime-index``) and pip runs ``--isolated``, so no configuration file or environment
      variable on the device can add an index.
    - The requested version must look like a version (``VERSION_PATTERN``) before it is placed in
      an argument list, and it is a single argument; nothing is built from it with a shell.
    - A failure is a sentence for the Queen that names no path, host, argument or output; the
      output of the failed program is in the prepare log on the device.
    - A step that is cancelled or times out ends its whole process group before it returns.

See Also:
    - docs/adr/0052-runtime-ladder-warden-moves-first.md for PREPARE.
    - pollen.bootstrap.warden for START and WITHDRAW.
    - docs/waggle/gateway-contract.md section 17.
"""

from __future__ import annotations

import asyncio
import os
import re
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import IO

from pollen.errors import ColonizeFailedError
from pollen.executors.base import ExecutorDeps
from pollen.platform.base import ProcessRef, SpawnSpec, stop_group
from pollen.state import PRIVATE_DIR_MODE, PRIVATE_FILE_MODE, InstallSettings

MIN_PYTHON = (3, 12)  # The oldest interpreter the hivemind runtime supports.
ENTRY_POINT = "hivemind-in-cell"  # The console script that runs the Warden; hivemind installs it.
VERSION_PATTERN = re.compile(r"[0-9][0-9A-Za-z.+!_-]{0,63}")  # PEP 440 characters, nothing else.
PROBE_TIMEOUT_S = 15.0  # Asking an interpreter or the environment its version: well under this.
VENV_TIMEOUT_S = 300.0  # Making an environment, ensurepip included, on a small board.
PIP_TIMEOUT_S = 3600.0  # An install from an index over a slow link; the Queen's deadline cuts it.
REAP_WAIT_S = 5.0  # How long an ended process may take to be reaped before it is left.

# Variables that make a child interpreter use the packet's libraries instead of its own.
_INTERPRETER_VARIABLES = frozenset({"PYTHONHOME", "PYTHONPATH", "VIRTUAL_ENV", "PYTHONSTARTUP"})
# What a single-file build's bootloader changes for its children, and where it keeps the original.
_LOADER_VARIABLES = ("LD_LIBRARY_PATH", "DYLD_LIBRARY_PATH", "LIBPATH")
_PROBE_CODE = "import sys; print(sys.version_info[0], sys.version_info[1])"
_VERSION_CODE = "from importlib.metadata import version; print(version('hivemind'))"

__all__ = [
    "ENTRY_POINT",
    "MIN_PYTHON",
    "VERSION_PATTERN",
    "child_environment",
    "entry_point",
    "installed_version",
    "prepare_runtime",
]


def child_environment(base: Mapping[str, str]) -> dict[str, str]:
    """Return the environment a program the packet starts runs with.

    Args:
        base: The packet's own environment.

    Returns:
        A copy without the variables that would point a child interpreter at the packet's
        libraries and, for a frozen build, with the loader paths its bootloader set put back.
    """
    env = {name: value for name, value in base.items() if name not in _INTERPRETER_VARIABLES}
    # A single-file build points the dynamic loader at the libraries it unpacked and keeps the
    # original value in NAME_ORIG; a system Python started with the unpacked ones can fail to load.
    if getattr(sys, "frozen", False):
        for name in _LOADER_VARIABLES:
            original = env.pop(f"{name}_ORIG", None)
            if original is not None:
                env[name] = original
            else:
                env.pop(name, None)
    return env


def entry_point(deps: ExecutorDeps) -> Path:
    """Return where the installed runtime's Warden entry point is (or would be)."""
    return deps.shim.venv_executable(deps.layout.venv, ENTRY_POINT)


async def installed_version(deps: ExecutorDeps) -> str | None:
    """Ask the runtime's environment which hivemind version it holds.

    Args:
        deps: The packet's collaborators (platform shim and data layout).

    Returns:
        The installed version, or None when there is no environment or no hivemind in it.
    """
    python = deps.shim.venv_executable(deps.layout.venv, "python")
    if not await asyncio.to_thread(python.is_file):
        return None
    text = await _capture(deps, [str(python), "-c", _VERSION_CODE])
    return text.strip() or None if text is not None else None


async def prepare_runtime(deps: ExecutorDeps, version: str) -> None:
    """Install ``hivemind==version`` into the runtime environment, unless it is already there.

    Args:
        deps: The packet's collaborators; the install settings are read from the state now.
        version: The version the Queen's order pins.

    Raises:
        ColonizeFailedError: No source was recorded, no interpreter is new enough, a program
            failed or timed out, or what was installed is not the version asked for.
    """
    if await installed_version(deps) == version and await asyncio.to_thread(
        entry_point(deps).is_file
    ):
        return  # Prepared already: nothing to fetch, and a running Warden's files stay untouched.
    settings = deps.state.current.install
    source = _source_arguments(settings)
    await asyncio.to_thread(_make_runtime_dir, deps.layout.runtime)
    log = await asyncio.to_thread(_open_log, deps.layout.prepare_log)
    try:
        await _ensure_environment(deps, settings, log)
        python = str(deps.shim.venv_executable(deps.layout.venv, "python"))
        install = [python, "-m", "pip", "--isolated", "install", "--no-input"]
        install += ["--disable-pip-version-check", *source, f"hivemind=={version}"]
        await _run(deps, install, log, PIP_TIMEOUT_S, "install the runtime")
    finally:
        log.close()
    await _verify(deps, version)


def _source_arguments(settings: InstallSettings) -> list[str]:
    """Return the pip arguments that name the only place code may come from."""
    if settings.runtime_wheelhouse is not None:
        # A wheelhouse is complete (hivemind and everything it needs): pip may look nowhere else.
        return ["--no-index", "--find-links", settings.runtime_wheelhouse]
    if settings.runtime_index is not None:
        return ["--index-url", settings.runtime_index]
    raise ColonizeFailedError(
        "No runtime source was recorded; run pollen install with --runtime-wheelhouse or "
        "--runtime-index on the device."
    )


async def _ensure_environment(
    deps: ExecutorDeps, settings: InstallSettings, log: IO[bytes]
) -> None:
    """Make the virtual environment, unless a working one is already there."""
    python = deps.shim.venv_executable(deps.layout.venv, "python")
    if await asyncio.to_thread(python.is_file) and await _is_new_enough([str(python)], deps):
        return
    base = await _find_python(deps, settings)
    # --clear makes a broken or older-Python environment over instead of building into it.
    await _run(
        deps,
        [*base, "-m", "venv", "--clear", str(deps.layout.venv)],
        log,
        VENV_TIMEOUT_S,
        "create the environment",
    )


async def _find_python(deps: ExecutorDeps, settings: InstallSettings) -> list[str]:
    """Return the command for a Python 3.12 or newer: the recorded one, else the first found."""
    if settings.runtime_python is not None:
        if await _is_new_enough([settings.runtime_python], deps):
            return [settings.runtime_python]
        raise ColonizeFailedError(
            "The interpreter recorded with --runtime-python is missing or older than Python "
            f"{MIN_PYTHON[0]}.{MIN_PYTHON[1]}."
        )
    for command in deps.shim.python_candidates():
        if await _is_new_enough(list(command), deps):
            return list(command)
    raise ColonizeFailedError(
        f"No Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]} or newer was found on the device; install "
        "one, or record its path with pollen install --runtime-python."
    )


async def _is_new_enough(command: Sequence[str], deps: ExecutorDeps) -> bool:
    """Say whether ``command`` starts a Python at least ``MIN_PYTHON``."""
    text = await _capture(deps, [*command, "-c", _PROBE_CODE])
    if text is None:
        return False  # Not installed, not runnable, or hung: not a candidate.
    parts = text.split()
    return (
        len(parts) == 2
        and all(part.isdigit() for part in parts)
        and ((int(parts[0]), int(parts[1])) >= MIN_PYTHON)
    )


async def _verify(deps: ExecutorDeps, version: str) -> None:
    """Check that what was installed is the version asked for, with its entry point."""
    installed = await installed_version(deps)
    if installed != version:
        raise ColonizeFailedError(
            "The runtime installed is not the version the order pins; the source must hold "
            "exactly that version of hivemind and everything it needs."
        )
    if not await asyncio.to_thread(entry_point(deps).is_file):
        raise ColonizeFailedError("The runtime installed has no Warden entry point.")


# ──────────────────────────────────────────────────────────────────────────────
# Running programs
# ──────────────────────────────────────────────────────────────────────────────


def _make_runtime_dir(path: Path) -> None:
    """Create the runtime directory (owner-only) if it is not there yet."""
    path.mkdir(mode=PRIVATE_DIR_MODE, parents=True, exist_ok=True)


def _open_log(path: Path) -> IO[bytes]:
    """Open the prepare log fresh and owner-only; its previous run is not worth keeping."""
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, PRIVATE_FILE_MODE)
    return os.fdopen(descriptor, "wb")


async def _run(
    deps: ExecutorDeps, argv: Sequence[str], log: IO[bytes], timeout_s: float, what: str
) -> None:
    """Run one step to its end with its output in ``log``, or raise why it could not."""
    try:
        process, ref = await _launch(deps, argv, stdout=log)
    except OSError:
        raise ColonizeFailedError(f"Could not {what}: the program could not be started.") from None
    try:
        # External await: a local program with a named ceiling; the Queen's deadline cuts it
        # sooner, and a cancelled or timed-out step is ended whole by the finally below.
        async with asyncio.timeout(timeout_s):
            code = await process.wait()
    except TimeoutError:
        raise ColonizeFailedError(
            f"Could not {what}: it took longer than {timeout_s:g} seconds."
        ) from None
    finally:
        await _end(deps, process, ref)
    if code != 0:
        raise ColonizeFailedError(
            f"Could not {what}: it exited with status {code}; its output is in the prepare log "
            "on the device."
        )


async def _capture(deps: ExecutorDeps, argv: Sequence[str]) -> str | None:
    """Run a short program and return its standard output, or None if it did not succeed."""
    try:
        process, ref = await _launch(deps, argv, stdout=asyncio.subprocess.PIPE)
    except OSError:
        return None
    try:
        # External await: an interpreter printing one line; a hang is the candidate's failure.
        async with asyncio.timeout(PROBE_TIMEOUT_S):
            output, _ = await process.communicate()
    except TimeoutError:
        return None
    finally:
        await _end(deps, process, ref)
    return output.decode("utf-8", errors="replace") if process.returncode == 0 else None


async def _launch(
    deps: ExecutorDeps, argv: Sequence[str], *, stdout: int | IO[bytes]
) -> tuple[asyncio.subprocess.Process, ProcessRef]:
    """Start ``argv`` as the leader of a process group of its own, output to ``stdout``."""
    process = await deps.shim.spawn(
        SpawnSpec(
            argv=tuple(argv),
            cwd=deps.layout.root,
            env=child_environment(os.environ),
            stdin=asyncio.subprocess.DEVNULL,
            stdout=stdout,
            stderr=asyncio.subprocess.STDOUT
            if stdout != asyncio.subprocess.PIPE
            else asyncio.subprocess.DEVNULL,
        )
    )
    return process, deps.shim.process_ref(process.pid)


async def _end(deps: ExecutorDeps, process: asyncio.subprocess.Process, ref: ProcessRef) -> None:
    """End ``process``'s whole group if it is still running, then reap it."""
    if process.returncode is None:
        await stop_group(deps.shim, ref, graceful=False, clock=deps.clock)
        try:
            async with asyncio.timeout(REAP_WAIT_S):
                await process.wait()
        except TimeoutError:
            pass  # Killed but not yet reaped; the loop's child watcher collects it later.
