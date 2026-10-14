"""Build a dummy hivemind runtime for the colonization tests: a wheelhouse, and a stub Warden.

Colonization installs the ``hivemind`` package into a virtual environment and then starts its
``hivemind-in-cell`` program. The pollen tests must exercise that for real (a real ``venv``, a real
``pip install --no-index --find-links``, a real background process) without building the real
runtime, so this kit builds a tiny ``hivemind`` wheel by hand: a package that reports the version
asked for, and whose console script is a stub Warden. The stub records the ``HIVEMIND_*`` variables
it was started with, signals when it is ready, leaves a marker when it is told to stop, and can be
told (by a variable the test sets) to die at once or to ignore the request to stop, and it says one
line on its standard output so the log it is given has something in it. ``fake_runtime``
writes the same two programs straight into a data directory as a finished environment, for the tests
that are about the Warden's process rather than the installation.

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Used by the bootstrap tests; depends on pollen's
    data layout and the standard library only.

Key invariants:
    - The wheel is a valid wheel (a RECORD with hashes), so pip installs it without complaint.
    - The stub never reads anything but its environment and never writes outside its state
      directory.

See Also:
    - pollen.bootstrap for the code under test.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import stat
import sys
import zipfile
from pathlib import Path

from pollen.platform import PlatformShim
from pollen.state import DataLayout

__all__ = ["STUB_VERSION", "build_wheelhouse", "fake_runtime", "stub_source", "wait_for_file"]

STUB_VERSION = "0.0.1"  # The version the dummy wheel and the fake runtime report.
_POLL_S = 0.02  # How often wait_for_file looks.

_STUB = """
import json
import os
import signal
import sys
import time
from pathlib import Path


def main():
    state = Path(os.environ["HIVEMIND_STATE_DIR"])
    state.mkdir(parents=True, exist_ok=True)
    mode = os.environ.get("HIVEMIND_STUB_MODE", "run")
    env = {k: v for k, v in os.environ.items() if k.startswith("HIVEMIND_")}
    (state / "env.json").write_text(json.dumps(env))
    print("stub warden started", flush=True)
    if mode == "exit":
        sys.exit(3)
    if mode == "ignore-term":
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
    else:
        def stop(*_):
            (state / "stopped").write_text("term")
            sys.exit(0)
        signal.signal(signal.SIGTERM, stop)
    (state / "ready").write_text(str(os.getpid()))
    while True:
        time.sleep(0.05)
"""


def stub_source() -> str:
    """Return the stub Warden's source, for a test to read what it is expected to do."""
    return _STUB


def _record_line(path: str, data: bytes) -> str:
    """Return one line of a wheel's RECORD: the file, its sha256 and its size."""
    digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode("ascii")
    return f"{path},sha256={digest},{len(data)}"


def build_wheelhouse(
    directory: Path, version: str = STUB_VERSION, *, with_entry_point: bool = True
) -> Path:
    """Write a one-wheel wheelhouse holding the dummy ``hivemind`` ``version``.

    Args:
        directory: Where the wheelhouse goes; created if it is missing.
        version: The version the wheel reports.
        with_entry_point: Whether the wheel installs the ``hivemind-in-cell`` program; a runtime
            without it is one the packet can install but not start.

    Returns:
        ``directory``, ready for ``pollen install --runtime-wheelhouse``.
    """
    directory.mkdir(parents=True, exist_ok=True)
    info = f"hivemind-{version}.dist-info"
    files = {
        "hivemind/__init__.py": f'__version__ = "{version}"\n'.encode(),
        "hivemind/stub.py": _STUB.encode(),
        f"{info}/METADATA": f"Metadata-Version: 2.1\nName: hivemind\nVersion: {version}\n".encode(),
        f"{info}/WHEEL": b"Wheel-Version: 1.0\nGenerator: pollen-tests\nRoot-Is-Purelib: true\n"
        b"Tag: py3-none-any\n",
        f"{info}/entry_points.txt": b"[console_scripts]\nhivemind-in-cell = hivemind.stub:main\n",
    }
    if not with_entry_point:
        del files[f"{info}/entry_points.txt"]
    record = "\n".join(
        [*(_record_line(name, data) for name, data in files.items()), f"{info}/RECORD,,"]
    )
    wheel = directory / f"hivemind-{version}-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        for name, data in files.items():
            archive.writestr(name, data)
        archive.writestr(f"{info}/RECORD", record + "\n")
    return directory


def fake_runtime(layout: DataLayout, shim: PlatformShim, version: str = STUB_VERSION) -> None:
    """Write a finished runtime environment into ``layout``: a ``python`` and the stub Warden.

    The ``python`` answers the two questions the packet asks of it (its own version, and which
    hivemind it holds); the Warden is the stub, run by this very interpreter.

    Args:
        layout: The data directory to write the environment into.
        shim: The platform shim, which knows where an environment keeps its programs.
        version: The hivemind version the environment reports.
    """
    python = shim.venv_executable(layout.venv, "python")
    warden = shim.venv_executable(layout.venv, "hivemind-in-cell")
    python.parent.mkdir(parents=True, exist_ok=True)
    python.write_text(
        f"#!{sys.executable}\nimport sys\n"
        f"code = sys.argv[2] if len(sys.argv) > 2 else ''\n"
        f"print({version!r} if 'importlib.metadata' in code else '3 12')\n",
        encoding="utf-8",
    )
    warden.write_text(f"#!{sys.executable}\n{_STUB}\nmain()\n", encoding="utf-8")
    for program in (python, warden):
        program.chmod(program.stat().st_mode | stat.S_IXUSR)


async def wait_for_file(path: Path, *, wait_s: float = 10.0) -> None:
    """Wait until ``path`` exists, polling a few times a second, or fail the test.

    Args:
        path: The file a started program is expected to write.
        wait_s: Real seconds to wait.

    Raises:
        AssertionError: The file never appeared.
    """
    # A bounded poll of the filesystem: the program under test writes it on its own schedule,
    # and there is no event to wait on.
    for _ in range(int(wait_s / _POLL_S)):
        if await asyncio.to_thread(path.exists):
            return
        await asyncio.sleep(_POLL_S)
    raise AssertionError(f"{path.name} never appeared.")
