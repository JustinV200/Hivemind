"""Define PackageLoader: unpack a verified package into scratch and import its tool by path.

A Worker's Comb tool is a package (`tool.py`, `test_tool.py`, `spec.json`) named by its SHA-256.
Once `CombToolRunner` has checked the bytes against the entry's hash, `PackageLoader` turns them
into a callable: it unpacks them (the package format refuses unsafe paths and bombs), checks that
the `spec.json` inside names the same tool as the entry, writes the files under
`comb-tools/<sha256>/` in the Worker's scratch through its `CellSession` (never by writing to
the disk directly), and imports `tool.py` from there under a module name unique to this Worker and
package. The module is cached per (Worker, hash), so a second call does not import again.

Fits into the Hive:
    Layer 4 (roles that do the work), inside `hivemind.workers.tools.comb`. Owned by a
    `CombToolBundle`; called by `CombToolRunner`. Calls into `hivemind.cell` (the session),
    `hivemind.royal_jelly.spec` (unpack, the spec) and the standard library's importer.

Key invariants:
    - Nothing is imported until the caller has verified the package hash; this class does not
      trust its input beyond that, and re-checks the package's own shape.
    - The importer reads the file the session just wrote, so the Worker's process and its Cell
      share a filesystem (true of the local and in-Cell sessions).
    - A refusal is a `ToolLoadError` carrying a stable `code`; nothing partial stays cached.

See Also:
    - docs/adr/0048 (inert blobs): this is the one place bytes become code, and only via an entry.
    - hivemind.workers.tools.comb.invoke for the caller that verifies hashes and runs the tool.
"""

from __future__ import annotations

import importlib.util
import inspect
import sys
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING, ClassVar, Protocol, cast

from pydantic import ValidationError

from hivemind.cell import CellSession
from hivemind.llm import JsonObject
from hivemind.royal_jelly.errors import PackageError
from hivemind.royal_jelly.spec import (
    SPEC_FILE,
    TOOL_MODULE,
    ToolSpec,
    required_files_present,
    unpack,
)
from hivemind.workers.errors import WorkerError
from waggle.ids import WorkerId

if TYPE_CHECKING:
    from hivemind.workers.tools.comb.entry import CombToolEntry

COMB_TOOLS_DIR = "comb-tools"  # Under scratch: one directory per package hash.
MODULE_PREFIX = "hivemind_comb_tool"  # Every imported tool module's name starts with this.

type ToolCallable = Callable[[CellSession, JsonObject], Awaitable[object]]

__all__ = [
    "COMB_TOOLS_DIR",
    "MODULE_PREFIX",
    "ModuleImporter",
    "PackageLoader",
    "PathImporter",
    "ToolCallable",
    "ToolLoadError",
]


class ToolLoadError(WorkerError):
    """Raise when a verified package still cannot be loaded as the tool its entry names."""

    code: ClassVar[str] = "hivemind.workers.tools.load_failed"

    def __init__(self, reason: str, detail: str) -> None:
        """Build the error.

        Args:
            reason: A stable short code (`bad_package`, `spec_mismatch`, `no_entry_point`,
                `import_failed`); the trail's `tool.load_refused` carries it.
            detail: One sentence for a reader; never the package's own text.
        """
        super().__init__(detail)
        self.reason = reason


class ModuleImporter(Protocol):
    """Import a Python file as a module under a given name; the seam tests may replace."""

    def import_module(self, name: str, path: Path) -> ModuleType:
        """Execute the file at `path` as module `name` and return it."""
        ...


class PathImporter:
    """The real importer: `importlib` by file path, registered in `sys.modules` under `name`."""

    def import_module(self, name: str, path: Path) -> ModuleType:
        """Import `path` as `name`; remove it from `sys.modules` again when it fails to execute.

        Args:
            name: A module name unique to this Worker and package.
            path: The `tool.py` the session wrote.

        Returns:
            The executed module.

        Raises:
            ToolLoadError: The file cannot be turned into a module, or raises while it executes.
        """
        spec = importlib.util.spec_from_file_location(name, path)
        if spec is None or spec.loader is None:
            raise ToolLoadError("import_failed", f"{path.name} cannot be imported as a module.")
        module = importlib.util.module_from_spec(spec)
        # Registered before it runs, as the import machinery does, so a tool that uses a
        # dataclass or a typing helper finds its own module by name.
        sys.modules[name] = module
        try:
            spec.loader.exec_module(module)
        except Exception as exc:
            # SAFETY: this executes a tool module that has passed the Quarantine Comb but is still
            # model-written code; any error at import time is reported as a refusal to load, never
            # allowed to take the Worker's attempt down.
            sys.modules.pop(name, None)
            raise ToolLoadError(
                "import_failed", f"importing the tool raised {type(exc).__name__}."
            ) from exc
        return module


@dataclass(frozen=True, slots=True)
class _CacheKey:
    """The identity of one loaded package for one Worker."""

    worker_id: WorkerId
    sha256: str


class PackageLoader:
    """Unpack, check and import Comb tool packages, caching each per Worker and hash.

    Owns its cache in place (codingrules 8.5): `_loaded` grows by one module per (Worker, hash).
    """

    def __init__(self, importer: ModuleImporter | None = None) -> None:
        """Build a loader.

        Args:
            importer: How a file becomes a module; the real `PathImporter` when omitted.
        """
        self._importer: ModuleImporter = importer if importer is not None else PathImporter()
        self._loaded: dict[_CacheKey, ToolCallable] = {}

    def cached(self, worker_id: WorkerId, entry: CombToolEntry) -> ToolCallable | None:
        """Return the callable this Worker already loaded for `entry`, or None.

        Takes the entry, never a bare hash: a cached callable was verified against the entry's
        hash when it first loaded, so a caller that finds one need not fetch or re-hash the
        package again.
        """
        return self._loaded.get(_CacheKey(worker_id, entry.package_sha256))

    async def load(
        self, session: CellSession, worker_id: WorkerId, entry: CombToolEntry, package: bytes
    ) -> ToolCallable:
        """Return the entry's callable, loading the verified `package` the first time.

        Args:
            session: The Worker's session; the files are written to its scratch through it.
            worker_id: The Worker, so the module name and the cache are per Worker.
            entry: The entry the package was verified against.
            package: The package bytes, whose SHA-256 the caller has already checked.

        Returns:
            The tool's `async def run(session, arguments)`.

        Raises:
            ToolLoadError: The package is unsafe or malformed, names a different tool than the
                entry, lacks its entry point, or fails to import.
        """
        cached = self.cached(worker_id, entry)
        if cached is not None:
            return cached
        files = _checked_files(package, entry)
        directory = Path(COMB_TOOLS_DIR) / entry.package_sha256
        # Local file I/O through the session, microseconds to milliseconds; a failure surfaces as
        # the session's own typed error, which the runner reports as a refusal.
        for name, data in files.items():
            await session.put_file(directory / name, data)
        module_name = f"{MODULE_PREFIX}_{worker_id}_{entry.package_sha256[:16]}"
        module = self._importer.import_module(
            module_name, session.scratch_dir / directory / TOOL_MODULE
        )
        run = _entry_callable(module, entry)
        self._loaded[_CacheKey(worker_id, entry.package_sha256)] = run
        return run


def _checked_files(package: bytes, entry: CombToolEntry) -> dict[str, bytes]:
    """Unpack `package` and check it holds the tool its entry names; else raise ToolLoadError."""
    try:
        files = dict(unpack(package))
    except PackageError as exc:
        raise ToolLoadError("bad_package", "the package is not a safe tool archive.") from exc
    missing = required_files_present(files)
    if missing:
        raise ToolLoadError("bad_package", f"the package lacks {', '.join(missing)}.")
    try:
        inner = ToolSpec.from_json(files[SPEC_FILE].decode("utf-8"))
    except (ValidationError, UnicodeDecodeError) as exc:
        raise ToolLoadError("bad_package", "the package's spec.json is not a valid spec.") from exc
    # The registry row, not the archive, decided what this entry is; an archive that says it is
    # another tool or version is a swapped package, whatever its hash.
    if (inner.name, inner.version, inner.tool_id) != (entry.name, entry.version, entry.tool_id):
        raise ToolLoadError("spec_mismatch", "the package's spec.json names a different tool.")
    return files


def _entry_callable(module: ModuleType, entry: CombToolEntry) -> ToolCallable:
    """Return the entry point attribute of `module`, which must be an async function."""
    attribute = entry.entry_point.partition(":")[2]
    run = getattr(module, attribute, None)
    if run is None or not inspect.iscoroutinefunction(run):
        raise ToolLoadError(
            "no_entry_point", f"the package has no async function {entry.entry_point!r}."
        )
    # getattr is Any; it was just proven to be an async function, which is what ToolCallable is.
    return cast(ToolCallable, run)
