"""Prove that a blob is inert: a hash runs nothing unless a Comb tool entry names it (ADR-0048).

Holding a package's bytes, or even serving them from a `PackageSource`, grants no way to run them.
The only road from bytes to code in a Worker is `CombToolRunner`, which is built from a
`CombToolEntry` (a Warden's reading of a registry row), verifies the entry's hash before it
unpacks anything and re-checks the Worker's capabilities on every call. These tests pin that rule
from four sides: the public surface takes entries and never hashes, a package nobody named is
never offered or imported, a package whose bytes were swapped is refused and recorded, and a
Worker without a capability the tool needs is refused with a `guard.denied` row.

Fits into the Hive:
    Mirrors src/hivemind/workers/tools/comb/invoke.py and loader.py (codingrules section 3);
    the Basket side of the same rule is tests/unit/basket/test_inert.py.

Key invariants:
    - None: this module holds tests only.

See Also:
    - docs/adr/0048-basket-inert-blobs-residency-and-transfer.md for the rule.
    - hivemind.workers.tools.comb.invoke for the module under test.
"""

from __future__ import annotations

import inspect
import sys
import typing
from collections.abc import Callable
from pathlib import Path
from typing import cast

import pytest
from builders.comb_tools import (
    FakePackageSource,
    make_comb_context,
    make_comb_package,
)
from builders.llm import make_tool_call
from builders.workers import make_assignment, run_tool

from hivemind.pheromone import TrailQuery
from hivemind.workers.tools import build_registry
from hivemind.workers.tools import comb as comb_package
from hivemind.workers.tools.comb import (
    CombToolEntry,
    CombToolRunner,
    PackageLoader,
    comb_tool_spec,
)
from hivemind.workers.tools.comb import invoke as invoke_module
from hivemind.workers.tools.comb.loader import MODULE_PREFIX
from hivemind.workers.tools.registry import ToolInvocation, ToolOutput

_HASH_WORDS = ("sha", "hash", "digest")  # No parameter of the public surface may be named so.
_MARKER_SOURCE = (
    "from pathlib import Path\n\n\nasync def run(session, arguments):\n"
    "    await session.put_file(Path('ran.txt'), b'ran')\n    return {'ran': True}\n"
)


# Plain data: an entry carries a hash by design (it is the thing that names a package), and a
# retain deposit describes a file the gate has just verified. Neither makes anything runnable.
_DATA_VALUES = frozenset({"CombToolEntry", "RetainDeposit"})


def _public_callables() -> list[tuple[str, object]]:
    """Every public function and class the invoke and comb faces export, but for data values."""
    found: list[tuple[str, object]] = []
    for module in (invoke_module, comb_package):
        for name in module.__all__:
            obj = getattr(module, name)
            if name not in _DATA_VALUES and (inspect.isfunction(obj) or inspect.isclass(obj)):
                found.append((f"{module.__name__}.{name}", obj))
    return found


def _signature_of(obj: object) -> inspect.Signature:
    return inspect.signature(cast(Callable[..., object], obj))


@pytest.mark.parametrize(("label", "obj"), _public_callables())
def test_no_public_function_or_constructor_takes_a_bare_hash(label: str, obj: object) -> None:
    names = [name.lower() for name in _signature_of(obj).parameters]

    offending = [n for n in names if any(word in n for word in _HASH_WORDS)]
    # request_tool and retain take a request / a file, never an existing package's hash.
    assert offending == [], f"{label} takes {offending}: a hash must reach code only via an entry"


def test_the_ways_to_make_runnable_code_all_demand_a_comb_tool_entry() -> None:
    hints = typing.get_type_hints(comb_tool_spec)
    runner_hints = typing.get_type_hints(CombToolRunner.__init__)
    # The loader imports the entry type only for annotations (it would cycle at runtime).
    names = {"CombToolEntry": CombToolEntry}
    loader_hints = typing.get_type_hints(PackageLoader.load, localns=names)
    cached_hints = typing.get_type_hints(PackageLoader.cached, localns=names)

    assert hints["entry"] is CombToolEntry
    assert runner_hints["entry"] is CombToolEntry
    assert loader_hints["entry"] is CombToolEntry
    assert cached_hints["entry"] is CombToolEntry


def test_the_package_source_protocol_only_hands_over_bytes_and_has_no_run_method() -> None:
    members = {name for name in dir(FakePackageSource) if not name.startswith("_")}

    assert "bytes_for" in members
    assert not {"run", "load", "execute", "install", "import_module"} & members


async def test_a_blob_nobody_named_is_neither_offered_nor_imported(tmp_path: Path) -> None:
    unnamed = make_comb_package(_MARKER_SOURCE, name="unnamed_tool")
    ctx = make_comb_context(tmp_path, [])  # an empty bundle ...
    assert isinstance(ctx.package_source, FakePackageSource)
    ctx.package_source.add(unnamed)  # ... though the source holds the bytes

    names = [d.name for d in build_registry(ctx).definitions()]
    invocation = ToolInvocation(ctx=ctx, assignment=make_assignment(ctx.clock))
    output = await build_registry(ctx).execute(
        invocation, make_tool_call(name="unnamed_tool", arguments={})
    )

    assert "unnamed_tool" not in names
    assert output.text == "no tool named 'unnamed_tool' is offered."
    assert ctx.package_source.asked == []  # nothing even fetched the bytes
    assert not [name for name in sys.modules if name.startswith(f"{MODULE_PREFIX}_{ctx.worker_id}")]
    with pytest.raises(FileNotFoundError):
        await ctx.session.get_file(Path("ran.txt"))


async def test_an_entry_removed_from_the_bundle_cannot_be_run_by_its_old_runner(
    tmp_path: Path,
) -> None:
    package = make_comb_package(_MARKER_SOURCE)
    ctx = make_comb_context(tmp_path, [package])
    assert ctx.comb_tools is not None and isinstance(ctx.package_source, FakePackageSource)
    spec = comb_tool_spec(package.entry, ctx.package_source, ctx.comb_tools.loader)
    ctx.comb_tools.replace(())

    result = await spec.run(ToolInvocation(ctx, make_assignment(ctx.clock)), {})

    assert isinstance(result, ToolOutput)
    assert "hive.tool.not_offered" in result.text
    assert ctx.package_source.asked == []


async def test_a_tampered_blob_is_refused_and_recorded_before_anything_unpacks(
    tmp_path: Path,
) -> None:
    package = make_comb_package(_MARKER_SOURCE)
    other = make_comb_package(_MARKER_SOURCE.replace("ran", "pwned"), name="echo_tool")
    ctx = make_comb_context(tmp_path, [package])
    assert isinstance(ctx.package_source, FakePackageSource)
    # The source serves different, perfectly valid bytes under the entry's hash.
    ctx.package_source.tamper(package.entry.package_sha256, other.package)

    output = await run_tool(ctx, "echo_tool", {})

    assert output.is_error is True and "hive.tool.hash_mismatch" in output.text
    refused = await ctx.trail.query(TrailQuery(kind="tool.load_refused"))
    assert len(refused) == 1
    assert refused[0].subject_id == package.entry.tool_id
    assert refused[0].payload["code"] == "hash_mismatch"
    assert refused[0].actor == str(ctx.worker_id)
    with pytest.raises(FileNotFoundError):  # nothing was written to scratch, nothing ran
        await ctx.session.get_file(Path("comb-tools") / package.entry.package_sha256 / "tool.py")
    assert "tool.loaded" not in [e.kind for e in await ctx.trail.query(TrailQuery(family="tool"))]


async def test_a_truncated_blob_is_refused_the_same_way(tmp_path: Path) -> None:
    package = make_comb_package(_MARKER_SOURCE)
    ctx = make_comb_context(tmp_path, [package])
    assert isinstance(ctx.package_source, FakePackageSource)
    ctx.package_source.tamper(package.entry.package_sha256, package.package[:-10])

    output = await run_tool(ctx, "echo_tool", {})

    assert "hive.tool.hash_mismatch" in output.text


async def test_a_worker_without_a_required_capability_is_denied_and_nothing_loads(
    tmp_path: Path,
) -> None:
    package = make_comb_package(_MARKER_SOURCE, required_capabilities=("net:example.org",))
    ctx = make_comb_context(tmp_path, [package])

    output = await run_tool(ctx, "echo_tool", {})

    assert output.is_error is True and "refused by the Guard" in output.text
    denied = await ctx.trail.query(TrailQuery(kind="guard.denied"))
    assert len(denied) == 1
    assert denied[0].payload["point"] == "tool_invocation"
    assert isinstance(ctx.package_source, FakePackageSource)
    assert ctx.package_source.asked == []
