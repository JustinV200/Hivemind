"""Tests for hivemind.workers.tools.comb.loader: unpack, check, write through the session, import.

Fits into the Hive:
    Mirrors src/hivemind/workers/tools/comb/loader.py (codingrules section 3). Packages are real;
    the session is a `DiskBackedSession` over `tmp_path`, so the real importer imports real files.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.workers.tools.comb.loader for the module under test.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import ModuleType

import pytest
from builders.comb_tools import DiskBackedSession, make_comb_package

from hivemind.royal_jelly.spec import SPEC_FILE, TESTS_MODULE, TOOL_MODULE, pack
from hivemind.workers.tools.comb import CombToolEntry, PackageLoader, PathImporter, ToolLoadError
from hivemind.workers.tools.comb.loader import COMB_TOOLS_DIR, MODULE_PREFIX
from waggle.clock import FakeClock
from waggle.ids import new_worker_id


def _session(tmp_path: Path) -> DiskBackedSession:
    return DiskBackedSession(tmp_path / "scratch", FakeClock())


class _RecordingImporter:
    """An importer that records its calls and returns a stand-in module with a given `run`."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, Path]] = []

    def import_module(self, name: str, path: Path) -> ModuleType:
        self.calls.append((name, path))
        module = ModuleType(name)

        async def run(session: object, arguments: object) -> object:
            return arguments

        module.__dict__["run"] = run
        return module


async def test_load_writes_the_files_under_scratch_and_imports_tool_py_by_path(
    tmp_path: Path,
) -> None:
    package = make_comb_package()
    session = _session(tmp_path)
    importer = _RecordingImporter()
    worker_id = new_worker_id(FakeClock())

    await PackageLoader(importer).load(session, worker_id, package.entry, package.package)

    directory = session.scratch_dir / COMB_TOOLS_DIR / package.entry.package_sha256
    assert {path.name for path in directory.iterdir()} == {TOOL_MODULE, TESTS_MODULE, SPEC_FILE}
    ((name, path),) = importer.calls
    assert path == directory / TOOL_MODULE
    assert name.startswith(f"{MODULE_PREFIX}_{worker_id}_")


async def test_the_second_load_is_served_from_the_cache(tmp_path: Path) -> None:
    package = make_comb_package()
    # The first load really unpacks (then the cache answers), so it needs a writable scratch.
    session = DiskBackedSession(tmp_path, FakeClock())
    importer = _RecordingImporter()
    loader = PackageLoader(importer)
    worker_id = new_worker_id(FakeClock())

    first = await loader.load(session, worker_id, package.entry, package.package)
    second = await loader.load(session, worker_id, package.entry, b"")

    assert first is second and len(importer.calls) == 1
    assert loader.cached(worker_id, package.entry) is first
    assert loader.cached(new_worker_id(FakeClock()), package.entry) is None  # per Worker


async def test_the_real_importer_runs_a_real_module_and_unregisters_a_broken_one(
    tmp_path: Path,
) -> None:
    good = tmp_path / "good.py"
    good.write_text("async def run(session, arguments):\n    return 1\n")
    broken = tmp_path / "broken.py"
    broken.write_text("raise RuntimeError('at import')\n")
    importer = PathImporter()

    module = importer.import_module("hivemind_comb_tool_good_test", good)
    with pytest.raises(ToolLoadError, match="RuntimeError"):
        importer.import_module("hivemind_comb_tool_broken_test", broken)

    assert hasattr(module, "run") and "hivemind_comb_tool_good_test" in sys.modules
    assert "hivemind_comb_tool_broken_test" not in sys.modules
    sys.modules.pop("hivemind_comb_tool_good_test", None)


@pytest.mark.parametrize(
    ("package", "code"),
    [
        (b"not a zip", "bad_package"),
        (pack({TOOL_MODULE: b"x = 1\n"}), "bad_package"),
        (pack({TOOL_MODULE: b"", TESTS_MODULE: b"", SPEC_FILE: b"{}"}), "bad_package"),
    ],
)
async def test_an_unusable_archive_is_refused_as_a_bad_package(
    tmp_path: Path, package: bytes, code: str
) -> None:
    entry = make_comb_package().entry
    loader = PackageLoader(_RecordingImporter())

    with pytest.raises(ToolLoadError) as raised:
        await loader.load(_session(tmp_path), new_worker_id(FakeClock()), entry, package)

    assert raised.value.reason == code


async def test_a_package_for_a_different_version_is_a_spec_mismatch(tmp_path: Path) -> None:
    other = make_comb_package(version="2.0.0")
    entry: CombToolEntry = make_comb_package(version="1.0.0").entry
    loader = PackageLoader(_RecordingImporter())

    with pytest.raises(ToolLoadError) as raised:
        await loader.load(_session(tmp_path), new_worker_id(FakeClock()), entry, other.package)

    assert raised.value.reason == "spec_mismatch"
