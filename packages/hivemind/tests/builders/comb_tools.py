"""Build the pieces of a Worker that runs Comb tools: packages, entries, sources, sessions, sinks.

A Worker's Comb tool is a package (three files) named by its hash, handed over as a
`CombToolEntry` by its Warden and loaded from a `PackageSource`. These builders make a real,
loadable package from a few lines of tool source, the entry that names it, a package source that
serves (or tampers with) the bytes, a `CellSession` whose files also exist on the real disk so the
loader can import them by path, and the fakes for the two seams a Worker calls out through: the
tool channel and the Warden's retain sink. `make_comb_context` wires them into a `WorkerContext`.

Fits into the Hive:
    Test infrastructure (codingrules section 14.5), not shipped. Used by the unit tests of
    `hivemind.workers.tools.comb`, the runtime's tool channel and the bounded loop.

Key invariants:
    - Every package built here is a real deterministic archive of `tool.py`, `test_tool.py` and a
      `spec.json` that agrees with the entry, so the loader's own checks pass unless a test breaks
      one on purpose.
    - `DiskBackedSession` writes every `put_file` to the real directory too; tests give it a
      `tmp_path`, so nothing leaks outside it.

See Also:
    - tests/builders/royal_jelly.py for the spec and package builders this builds on.
    - hivemind.workers.tools.comb for the code under test.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from builders.royal_jelly import make_tool_spec
from builders.workers import make_context

from hivemind.cell.fake import FakeSession
from hivemind.guard import CapabilitySet
from hivemind.royal_jelly.spec import (
    SPEC_FILE,
    TESTS_MODULE,
    TOOL_MODULE,
    ToolSpec,
    pack,
    sha256_of,
)
from hivemind.workers.context import WorkerContext
from hivemind.workers.tools.comb import CombToolBundle, CombToolEntry, RetainDeposit
from hivemind.workers.tools.errors import PackageUnavailableError
from waggle.clock import Clock, FakeClock
from waggle.ids import WardenId, new_cell_id, new_warden_id
from waggle.messages.control import ErrorMessage
from waggle.messages.tool.authoring import (
    QuarantinePath,
    ToolPromoted,
    ToolRequest,
    ToolScope,
)

__all__ = [
    "ECHO_SOURCE",
    "CombPackage",
    "DiskBackedSession",
    "FakePackageSource",
    "FakeToolChannel",
    "RecordingRetainSink",
    "make_comb_context",
    "make_comb_package",
    "make_promoted",
]

ECHO_SOURCE = "async def run(session, arguments):\n    return {'echo': arguments}\n"
_TESTS_STUB = (
    "import unittest\n\n\nclass T(unittest.TestCase):\n    def test_ok(self):\n        pass\n"
)
_DEFAULT_COMB_CAPABILITIES = ("tool:*", "fs:read:**", "fs:write:scratch/**", "exec:*")


@dataclass(frozen=True, slots=True)
class CombPackage:
    """A built tool: the spec, the package bytes and the entry that names them."""

    spec: ToolSpec
    package: bytes
    entry: CombToolEntry


def make_comb_package(source: str = ECHO_SOURCE, **spec_overrides: object) -> CombPackage:
    """Build a loadable Comb tool from `source`, with the spec's fields overridden.

    Args:
        source: The `tool.py` text; the default echoes its arguments.
        spec_overrides: Any `ToolSpec` field (a `name`, `supported_os`, `required_capabilities`...).
            The default needs no capability beyond the session.

    Returns:
        The spec, the real package bytes and the matching entry.
    """
    fields: dict[str, object] = {"required_capabilities": ()}
    fields.update(spec_overrides)
    spec = make_tool_spec(**fields)
    files = {
        TOOL_MODULE: source.encode(),
        TESTS_MODULE: _TESTS_STUB.encode(),
        SPEC_FILE: spec.to_json().encode(),
    }
    package = pack(files)
    entry = CombToolEntry(
        name=spec.name,
        version=spec.version,
        tool_id=spec.tool_id,
        description=spec.description,
        input_schema_json=spec.input_schema_json,
        required_capabilities=spec.required_capabilities,
        supported_os=spec.supported_os,
        package_sha256=sha256_of(package),
        package_size_bytes=len(package),
        scope=spec.scope,
        cell_id=spec.cell_id,
        entry_point=spec.entry_point,
    )
    return CombPackage(spec=spec, package=package, entry=entry)


class DiskBackedSession(FakeSession):
    """A `FakeSession` whose written files also exist on the real disk under its scratch.

    The loader imports a tool by file path, which an in-memory session cannot serve; scratch here
    is a real directory (a test's `tmp_path`), so both views agree.
    """

    async def put_file(self, path: Path, data: bytes) -> None:
        """Store `data` in memory like the fake, and write the same file on disk."""
        await super().put_file(path, data)
        _write(path if path.is_absolute() else self.scratch_dir / path, data)


def _write(target: Path, data: bytes) -> None:
    """Write `target`, creating its directories (a plain helper, not inside an async body)."""
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)


class FakePackageSource:
    """A `PackageSource` over a dict of blobs; `tamper` swaps bytes under a hash for a test."""

    def __init__(self, blobs: Mapping[str, bytes] | None = None) -> None:
        """Build the source over `blobs` (hash to bytes)."""
        self.blobs = dict(blobs or {})
        self.asked: list[str] = []

    def add(self, package: CombPackage) -> None:
        """Serve `package` under its entry's hash."""
        self.blobs[package.entry.package_sha256] = package.package

    def tamper(self, sha256: str, data: bytes) -> None:
        """Serve `data` under `sha256`, whatever it really hashes to."""
        self.blobs[sha256] = data

    async def bytes_for(self, sha256: str) -> bytes:
        """Return the blob, or raise `PackageUnavailableError` when none is held."""
        self.asked.append(sha256)
        if sha256 not in self.blobs:
            raise PackageUnavailableError(sha256, "not resident")
        return self.blobs[sha256]


type ToolAnswer = ToolPromoted | ErrorMessage


class FakeToolChannel:
    """A `ToolChannel` that records requests and answers from a value or a function."""

    def __init__(
        self, answer: ToolAnswer | Callable[[ToolRequest], ToolAnswer], warden_id: WardenId
    ) -> None:
        """Build the channel; `answer` is returned, or called with each request."""
        self._answer = answer
        self._warden_id = warden_id
        self.requests: list[ToolRequest] = []

    @property
    def warden_id(self) -> WardenId:
        """The Warden this fake link leads to."""
        return self._warden_id

    async def request(self, request: ToolRequest) -> ToolAnswer:
        """Record the request and return the scripted answer."""
        self.requests.append(request)
        return self._answer(request) if callable(self._answer) else self._answer


class RecordingRetainSink:
    """A `RetainSink` that records each deposit it is handed."""

    def __init__(self) -> None:
        """Build the sink with nothing deposited."""
        self.deposits: list[RetainDeposit] = []

    async def retain(self, deposit: RetainDeposit) -> None:
        """Record the deposit."""
        self.deposits.append(deposit)


def make_comb_context(
    scratch: Path,
    packages: Sequence[CombPackage] = (),
    clock: Clock | None = None,
    **overrides: object,
) -> WorkerContext:
    """Build a Worker context that holds `packages` as Comb tools over a real scratch directory.

    Args:
        scratch: A real directory (a test's `tmp_path` child) used as the session's scratch.
        packages: The tools in the Worker's bundle; each is also served by its package source.
        clock: Shared by every collaborator; a fresh FakeClock when omitted.
        overrides: Any `WorkerContext` field (a `capabilities` set, a `tools` channel...).

    Returns:
        A context with `comb_tools`, `package_source` and the default capabilities, which hold
        `tool:*` but neither `tool:request` nor any `net:` scope.
    """
    active = clock or FakeClock()
    source = FakePackageSource()
    for package in packages:
        source.add(package)
    fields: dict[str, object] = {
        "session": DiskBackedSession(scratch, active),
        "comb_tools": CombToolBundle(package.entry for package in packages),
        "package_source": source,
        "capabilities": CapabilitySet.parse(*_DEFAULT_COMB_CAPABILITIES),
    }
    fields.update(overrides)
    return make_context(active, **fields)


def make_promoted(package: CombPackage, clock: Clock | None = None) -> ToolPromoted:
    """Build the `tool.promoted` announcement for `package`'s tool, as the Queen would send it."""
    active = clock or FakeClock()
    entry = package.entry
    cell_scoped = entry.scope is ToolScope.CELL
    return ToolPromoted(
        tool_id=entry.tool_id,
        name=entry.name,
        version=entry.version,
        scope=entry.scope,
        cell_id=entry.cell_id,
        description=entry.description,
        input_schema_json=entry.input_schema_json,
        required_capabilities=entry.required_capabilities,
        supported_os=entry.supported_os,
        quarantine_path=QuarantinePath.SANDBOX_CELL,
        quarantined_on=new_cell_id(active),
        quarantine_passed_at=active.now(),
        package_sha256=entry.package_sha256,
        package_size_bytes=entry.package_size_bytes,
        promoted_by_warden=new_warden_id(active) if cell_scoped else None,
        supersedes_version=None,
        reason="Passed the Quarantine Comb.",
    )


def make_warden_id(clock: Clock | None = None) -> WardenId:
    """Mint a Warden id for a fake channel."""
    return new_warden_id(clock or FakeClock())
