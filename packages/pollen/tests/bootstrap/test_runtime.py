"""Test pollen.bootstrap.runtime: PREPARE installs the pinned runtime from the operator's source.

The tests run the real thing where it is cheap enough to: a real virtual environment and a real
``pip install --no-index --find-links`` of a dummy ``hivemind`` wheel built by hand
(``support/runtime_kit.py``), never the real runtime; the other recorded source, a package index,
is a plain HTTP server on loopback that serves the same wheel. The failure paths use small
stand-in interpreters so that no test waits for pip to fail slowly.

Fits into the Hive:
    Mirrors src/pollen/bootstrap/runtime.py (codingrules section 3).

Key invariants:
    - Nothing here reaches beyond this machine: the only source is a directory under
      ``tmp_path``, served on loopback when it is to be an index.
    - Every test that starts a program ends it, so no process outlives the test.

See Also:
    - pollen.bootstrap.runtime for the module under test.
"""

from __future__ import annotations

import asyncio
import contextlib
import functools
import http.server
import os
import stat
import sys
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest
from executor_kit import Kit, make_kit
from runtime_kit import STUB_VERSION, build_wheelhouse, wait_for_file

from pollen.bootstrap import runtime
from pollen.bootstrap.runtime import (
    child_environment,
    entry_point,
    installed_version,
    prepare_runtime,
)
from pollen.errors import ColonizeFailedError
from pollen.state import InstallSettings
from waggle.clock import Clock

posix_only = pytest.mark.skipif(
    sys.platform == "win32", reason="stand-in interpreters are POSIX scripts"
)


def _kit(tmp_path: Path, clock: Clock, install: InstallSettings) -> Kit:
    """A device whose install settings are ``install``."""
    return make_kit(tmp_path, clock, install=install)


def _script(path: Path, body: str) -> Path:
    """Write an executable Python script at ``path``: a stand-in for an interpreter."""
    path.write_text(f"#!{sys.executable}\nimport os, sys, time\n{body}\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


# ──────────────────────────────────────────────────────────────────────────────
# The environment programs run with
# ──────────────────────────────────────────────────────────────────────────────


def test_a_child_does_not_inherit_the_variables_that_point_at_the_packets_libraries() -> None:
    base = {"PATH": "/usr/bin", "PYTHONPATH": "/x", "PYTHONHOME": "/y", "VIRTUAL_ENV": "/v"}

    assert child_environment(base) == {"PATH": "/usr/bin"}


def test_a_frozen_builds_loader_paths_are_put_back_for_a_system_python(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    base = {
        "LD_LIBRARY_PATH": "/unpacked/_MEI123",  # What the bootloader set,
        "LD_LIBRARY_PATH_ORIG": "/opt/lib",  # and what it was before.
        "DYLD_LIBRARY_PATH": "/unpacked/_MEI123",  # Set with nothing before it: to be removed.
        "HOME": "/home/me",
    }

    env = child_environment(base)

    assert env == {"LD_LIBRARY_PATH": "/opt/lib", "HOME": "/home/me"}


def test_an_ordinary_install_keeps_its_loader_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delattr(sys, "frozen", raising=False)

    assert child_environment({"LD_LIBRARY_PATH": "/opt/lib"}) == {"LD_LIBRARY_PATH": "/opt/lib"}


# ──────────────────────────────────────────────────────────────────────────────
# PREPARE for real
# ──────────────────────────────────────────────────────────────────────────────


@posix_only
async def test_prepare_installs_the_pinned_version_and_does_nothing_the_second_time(
    tmp_path: Path, clock: Clock
) -> None:
    wheels = build_wheelhouse(tmp_path / "wheels")
    kit = _kit(tmp_path, clock, InstallSettings(runtime_wheelhouse=str(wheels)))

    await prepare_runtime(kit.deps, STUB_VERSION)

    assert await installed_version(kit.deps) == STUB_VERSION
    assert entry_point(kit.deps).is_file()
    assert kit.layout.prepare_log.is_file()
    # Prepared already: with the source gone, asking again must not reach for it.
    for wheel in wheels.iterdir():
        wheel.unlink()
    await prepare_runtime(kit.deps, STUB_VERSION)
    assert await installed_version(kit.deps) == STUB_VERSION

    # A version the source does not hold is a failure with the status, never the output.
    with pytest.raises(ColonizeFailedError, match=r"install the runtime.*status") as raised:
        await prepare_runtime(kit.deps, "9.9.9")
    assert str(tmp_path) not in str(raised.value)  # No path in what the Queen is told.


class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    """Serves files without writing a line to standard error for each request."""

    def log_message(self, format: str, *args: object) -> None:
        """Say nothing: a test's output is for its failures."""


@contextlib.contextmanager
def _served(directory: Path) -> Iterator[str]:
    """Serve ``directory`` on a loopback port while the block runs; yield the index's URL."""
    handler = functools.partial(_QuietHandler, directory=str(directory))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}/simple"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=10)


@posix_only
async def test_prepare_installs_from_a_package_index_when_that_is_the_recorded_source(
    tmp_path: Path, clock: Clock, monkeypatch: pytest.MonkeyPatch
) -> None:
    wheels = build_wheelhouse(tmp_path / "served")
    page = wheels / "simple" / "hivemind"  # A PEP 503 "simple" index with one project.
    page.mkdir(parents=True)
    wheel = f"hivemind-{STUB_VERSION}-py3-none-any.whl"
    (page / "index.html").write_text(f'<a href="../../{wheel}">{wheel}</a>\n', encoding="utf-8")
    for proxy in ("NO_PROXY", "no_proxy"):  # A proxy in the environment must not stand in between.
        monkeypatch.setenv(proxy, "127.0.0.1")

    with _served(wheels) as index:
        kit = _kit(tmp_path, clock, InstallSettings(runtime_index=index))
        await prepare_runtime(kit.deps, STUB_VERSION)

    assert await installed_version(kit.deps) == STUB_VERSION
    assert entry_point(kit.deps).is_file()


@posix_only
async def test_a_version_the_package_spells_otherwise_than_the_order_is_refused(
    tmp_path: Path, clock: Clock
) -> None:
    # pip's ``==1.0`` accepts 1.0.0, but a Warden is started only against the version the Queen
    # named, spelled as the package reports it, so the two can never be taken for each other.
    wheels = build_wheelhouse(tmp_path / "wheels", version="1.0.0")
    kit = _kit(tmp_path, clock, InstallSettings(runtime_wheelhouse=str(wheels)))

    with pytest.raises(ColonizeFailedError, match="not the version the order pins"):
        await prepare_runtime(kit.deps, "1.0")


@posix_only
async def test_a_runtime_without_the_warden_program_is_refused_after_the_install(
    tmp_path: Path, clock: Clock
) -> None:
    wheels = build_wheelhouse(tmp_path / "wheels", with_entry_point=False)
    kit = _kit(tmp_path, clock, InstallSettings(runtime_wheelhouse=str(wheels)))

    with pytest.raises(ColonizeFailedError, match="no Warden entry point"):
        await prepare_runtime(kit.deps, STUB_VERSION)


@posix_only
async def test_a_runtime_source_must_have_been_recorded(tmp_path: Path, clock: Clock) -> None:
    kit = _kit(tmp_path, clock, InstallSettings())

    with pytest.raises(ColonizeFailedError, match="No runtime source was recorded"):
        await prepare_runtime(kit.deps, STUB_VERSION)


@pytest.mark.parametrize("version", ["1.0 --index-url http://evil.example", "../x", "v1", ""])
def test_a_version_that_is_not_a_version_is_not_one(version: str) -> None:
    assert runtime.VERSION_PATTERN.fullmatch(version) is None


# ──────────────────────────────────────────────────────────────────────────────
# Finding an interpreter
# ──────────────────────────────────────────────────────────────────────────────


@posix_only
async def test_a_recorded_interpreter_that_is_too_old_is_refused_not_replaced(
    tmp_path: Path, clock: Clock
) -> None:
    old = _script(tmp_path / "python3.11", "print('3 11')")
    wheels = build_wheelhouse(tmp_path / "wheels")
    install = InstallSettings(runtime_wheelhouse=str(wheels), runtime_python=str(old))
    kit = _kit(tmp_path, clock, install)

    with pytest.raises(ColonizeFailedError, match=r"older than Python 3\.12"):
        await prepare_runtime(kit.deps, STUB_VERSION)


@posix_only
async def test_with_no_interpreter_new_enough_the_sentence_says_what_to_do(
    tmp_path: Path, clock: Clock, monkeypatch: pytest.MonkeyPatch
) -> None:
    wheels = build_wheelhouse(tmp_path / "wheels")
    kit = _kit(tmp_path, clock, InstallSettings(runtime_wheelhouse=str(wheels)))
    monkeypatch.setattr(kit.deps.shim, "python_candidates", lambda: (("python3.0-absent",),))

    with pytest.raises(ColonizeFailedError, match=r"No Python 3\.12 or newer.*--runtime-python"):
        await prepare_runtime(kit.deps, STUB_VERSION)


@posix_only
async def test_candidates_are_tried_in_order_and_the_first_new_enough_wins(
    tmp_path: Path, clock: Clock, monkeypatch: pytest.MonkeyPatch
) -> None:
    old = _script(tmp_path / "old", "print('3 9')")
    new = _script(tmp_path / "new", "print('3 13')")
    wheels = build_wheelhouse(tmp_path / "wheels")
    kit = _kit(tmp_path, clock, InstallSettings(runtime_wheelhouse=str(wheels)))
    monkeypatch.setattr(
        kit.deps.shim, "python_candidates", lambda: (("absent",), (str(old),), (str(new),))
    )

    found = await runtime._find_python(kit.deps, kit.deps.state.current.install)

    assert found == [str(new)]


@posix_only
async def test_an_interpreter_that_hangs_when_asked_its_version_is_passed_over(
    tmp_path: Path, clock: Clock, monkeypatch: pytest.MonkeyPatch
) -> None:
    hung = _script(tmp_path / "hung", "time.sleep(120)")
    new = _script(tmp_path / "new", "print('3 13')")
    wheels = build_wheelhouse(tmp_path / "wheels")
    kit = _kit(tmp_path, clock, InstallSettings(runtime_wheelhouse=str(wheels)))
    monkeypatch.setattr(runtime, "PROBE_TIMEOUT_S", 0.3)
    monkeypatch.setattr(kit.deps.shim, "python_candidates", lambda: ((str(hung),), (str(new),)))

    found = await runtime._find_python(kit.deps, kit.deps.state.current.install)

    assert found == [str(new)]


# ──────────────────────────────────────────────────────────────────────────────
# A step that does not finish
# ──────────────────────────────────────────────────────────────────────────────


def _slow_interpreter(tmp_path: Path) -> Path:
    """A stand-in Python that passes the version probe and then hangs on anything else."""
    return _script(
        tmp_path / "python3.12",
        "if sys.argv[1] == '-c':\n    print('3 12')\nelse:\n"
        "    open(os.environ['PIDFILE'], 'w').write(str(os.getpid()))\n    time.sleep(120)",
    )


@posix_only
async def test_a_step_that_overruns_its_ceiling_is_ended_and_reported(
    tmp_path: Path, clock: Clock, monkeypatch: pytest.MonkeyPatch
) -> None:
    pidfile = tmp_path / "pid"
    monkeypatch.setenv("PIDFILE", str(pidfile))
    monkeypatch.setattr(runtime, "VENV_TIMEOUT_S", 0.5)
    wheels = build_wheelhouse(tmp_path / "wheels")
    install = InstallSettings(
        runtime_wheelhouse=str(wheels), runtime_python=str(_slow_interpreter(tmp_path))
    )
    kit = _kit(tmp_path, clock, install)

    with pytest.raises(
        ColonizeFailedError, match=r"create the environment.*longer than 0.5 seconds"
    ):
        await prepare_runtime(kit.deps, STUB_VERSION)

    assert not kit.deps.shim.group_alive(kit.deps.shim.process_ref(int(pidfile.read_text())))


@posix_only
async def test_cancelling_a_prepare_ends_the_program_it_started(
    tmp_path: Path, clock: Clock, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The Queen's deadline cancels the step: no pip or venv may be left running behind it.
    pidfile = tmp_path / "pid"
    monkeypatch.setenv("PIDFILE", str(pidfile))
    wheels = build_wheelhouse(tmp_path / "wheels")
    install = InstallSettings(
        runtime_wheelhouse=str(wheels), runtime_python=str(_slow_interpreter(tmp_path))
    )
    kit = _kit(tmp_path, clock, install)
    task = asyncio.ensure_future(prepare_runtime(kit.deps, STUB_VERSION))
    await wait_for_file(pidfile)
    pid = int(pidfile.read_text())
    assert kit.deps.shim.group_alive(kit.deps.shim.process_ref(pid))

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert not kit.deps.shim.group_alive(kit.deps.shim.process_ref(pid))
    assert os.environ.get("PIDFILE") == str(pidfile)  # monkeypatch undoes it, not the code.
