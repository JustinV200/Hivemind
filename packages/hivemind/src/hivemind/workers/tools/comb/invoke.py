"""Define comb_tool_spec and CombToolRunner: offer a Comb tool to a model and run it safely.

This is the one place a promoted tool's bytes become code inside a Worker (ADR-0048: a blob is
inert), and it accepts only a `CombToolEntry` the Warden built from a registry row: there is no
function here, and none in the Worker's tool surface, that takes a bare hash. Before every call
the runner re-validates, in order: the entry is still in the Worker's bundle, the Cell's operating
system is one the tool was proved for (`hive.tool.os_unsupported`), the arguments against the
tool's schema, and every capability the tool needs against what the Worker holds, each refusal a
`guard.denied` row at the tool-invocation point. It then fetches the package from the
`PackageSource`, verifies its SHA-256 against the entry (a mismatch is refused and recorded as
`tool.load_refused`), loads it through the bundle's `PackageLoader`, runs
`await run(ctx.session, arguments)` under a bounded timeout, and returns the JSON-serialised result
bounded in size. The first load per Worker records `tool.loaded`.

Fits into the Hive:
    Layer 4 (roles that do the work), inside `hivemind.workers.tools.comb`. Offered by
    `hivemind.workers.tools.registry.build_registry` through `comb_specs`; the registry authorises
    `tool:<name>` and scans the result around it. Calls into `hivemind.guard`, `hivemind.llm`,
    `hivemind.royal_jelly` (hashes, events) and the sibling modules in this package.

Key invariants:
    - A call never runs unless, in this order, its entry is offered, its OS fits, its arguments fit
      its schema, the Worker holds every capability the tool needs and the package bytes hash to
      the entry's `package_sha256`.
    - The tool acts only through `ctx.session`; the runner hands it nothing else.
    - A tool that raises, times out or returns something unserialisable produces a readable error
      result, never an exception into the Worker's loop; a Worker control exception still passes.
    - A result is at most `MAX_COMB_RESULT_CHARS` characters, truncated with a visible marker.

See Also:
    - docs/adr/0046 (tool spec, Comb Registry and scopes) and docs/adr/0048 (inert blobs).
    - tests/unit/workers/tools/test_invoke_inert.py for the proof that a hash alone runs nothing.
    - hivemind.workers.tools.comb.loader for the unpack-and-import half.
"""

from __future__ import annotations

import asyncio
import copy
import json

from hivemind.common.logging import get_logger
from hivemind.guard import Capability, EnforcementPoint, InvalidCapabilityError
from hivemind.llm import JsonObject, ToolDefinition, validate_arguments
from hivemind.pheromone import PheromoneEvent
from hivemind.royal_jelly.promotion import (
    ToolIdentity,
    load_refused_event,
    loaded_event,
)
from hivemind.royal_jelly.spec import sha256_of
from hivemind.workers.context import WorkerContext
from hivemind.workers.errors import WorkerError
from hivemind.workers.tools.authorize import authorize, refusal_text
from hivemind.workers.tools.comb.entry import CombToolEntry, PackageSource
from hivemind.workers.tools.comb.loader import PackageLoader, ToolCallable, ToolLoadError
from hivemind.workers.tools.errors import PackageUnavailableError
from hivemind.workers.tools.registry import ToolInvocation, ToolOutput, ToolSpec
from waggle.messages.tool.authoring import ToolScope

COMB_TOOL_TIMEOUT_S = 60.0  # A generated tool finishes in seconds (the Comb's wall limit is 120).
MAX_COMB_RESULT_CHARS = 131_072  # 128 KiB of JSON text: the most one result may put in a prompt.
MAX_ERROR_DETAIL_CHARS = 300  # How much of a tool's own exception message a result repeats.
TRUNCATION_MARKER = "\n[output truncated at 131072 characters]"

__all__ = [
    "COMB_TOOL_TIMEOUT_S",
    "MAX_COMB_RESULT_CHARS",
    "TRUNCATION_MARKER",
    "CombToolRunner",
    "comb_specs",
    "comb_tool_spec",
]

log = get_logger(__name__)


def comb_tool_spec(entry: CombToolEntry, source: PackageSource, loader: PackageLoader) -> ToolSpec:
    """Build the `ToolSpec` that offers one Comb tool to a model and runs it.

    Args:
        entry: The tool, as its Warden's bundle names it.
        source: Where its package bytes come from (the resident Basket).
        loader: The Worker's import cache (`CombToolBundle.loader`).

    Returns:
        A spec whose definition comes from the entry and whose runner is a `CombToolRunner`.

    Raises:
        ValueError: The entry's schema is not a JSON object or its name is not a tool name; the
            caller skips such an entry rather than failing the whole registry.
    """
    schema = json.loads(entry.input_schema_json)
    if not isinstance(schema, dict):
        raise ValueError(f"The schema of tool {entry.name!r} is not a JSON object.")
    definition = ToolDefinition(name=entry.name, description=entry.description, parameters=schema)
    return ToolSpec(definition=definition, run=CombToolRunner(entry, source, loader, schema))


def comb_specs(ctx: WorkerContext, taken: frozenset[str]) -> tuple[ToolSpec, ...]:
    """Return the Comb tools `ctx` may be offered: its bundle's entries that fit its Cell.

    Args:
        ctx: The Worker's context; `comb_tools` and `package_source` must both be set.
        taken: Names already offered (the built-ins); a Comb tool never shadows one of them.

    Returns:
        One spec per usable entry, in bundle order. An entry for another Cell or another
        operating system, a name collision and an entry whose schema is unusable are skipped.
    """
    bundle, source = ctx.comb_tools, ctx.package_source
    if bundle is None or source is None:
        return ()
    specs: list[ToolSpec] = []
    names = set(taken)
    for entry in bundle.entries():
        # A promoted tool must not replace a built-in (a tool named read_file would hijack it) or
        # an earlier entry; the first holder of a name keeps it.
        if entry.name in names or not _fits_cell(entry, ctx):
            continue
        try:
            specs.append(comb_tool_spec(entry, source, bundle.loader))
        except ValueError as exc:
            # ValidationError is a ValueError: a bad definition skips this tool, not the registry.
            log.warning("workers.comb_tool_skipped", tool=entry.name, error=type(exc).__name__)
            continue
        names.add(entry.name)
    return tuple(specs)


class CombToolRunner:
    """Run one Comb tool: re-validate, load by verified hash, call, bound the result."""

    def __init__(
        self, entry: CombToolEntry, source: PackageSource, loader: PackageLoader, schema: JsonObject
    ) -> None:
        """Bind the runner to one entry.

        Args:
            entry: The tool this runner runs; the only thing that can name a package to load.
            source: Where the package bytes come from.
            loader: The Worker's import cache.
            schema: The entry's input schema, parsed once.
        """
        self._entry = entry
        self._source = source
        self._loader = loader
        self._schema = schema

    async def __call__(self, invocation: ToolInvocation, arguments: JsonObject) -> str | ToolOutput:
        """Validate, load and run the tool; see the module docstring for the order."""
        refusal = await self._refusal(invocation, arguments)
        if refusal is not None:
            return ToolOutput(text=refusal, is_error=True)
        loaded = await self._load(invocation)
        if isinstance(loaded, str):
            return ToolOutput(text=loaded, is_error=True)
        return await self._execute(loaded, invocation, arguments)

    async def _refusal(self, invocation: ToolInvocation, arguments: JsonObject) -> str | None:
        """Return the refusal line for a call that must not run, or None when every check holds."""
        ctx, entry = invocation.ctx, self._entry
        if ctx.comb_tools is not None and entry not in ctx.comb_tools.entries():
            return _line("not_offered", f"{entry.name!r} is no longer in this Worker's bundle.")
        if not _supports_os(entry, ctx):
            supported = ", ".join(os.value for os in entry.supported_os)
            return _line(
                "os_unsupported",
                f"{entry.name!r} supports {supported}; this Cell runs "
                f"{ctx.cell.capabilities.os.value}.",
            )
        errors = validate_arguments(self._schema, arguments)
        if errors:
            return _line("arguments_invalid", "; ".join(errors))
        return await self._capability_refusal(invocation)

    async def _capability_refusal(self, invocation: ToolInvocation) -> str | None:
        """Ask the Guard about every capability the tool needs; the first refusal is the answer."""
        for text in self._entry.required_capabilities:
            try:
                needed = Capability.parse(text)
            except InvalidCapabilityError:
                return _line("bad_capability", f"{self._entry.name!r} needs {text!r}, unparseable.")
            # The Guard records a refusal as guard.denied at the tool-invocation point.
            decision = await authorize(invocation, EnforcementPoint.TOOL_INVOCATION, needed)
            if not decision.allowed:
                return refusal_text(decision)
        return None

    async def _load(self, invocation: ToolInvocation) -> ToolCallable | str:
        """Return the tool's callable, loading it on first use; a refusal line when it cannot."""
        ctx, entry = invocation.ctx, self._entry
        cached = self._loader.cached(ctx.worker_id, entry)
        if cached is not None:
            return cached  # Verified when it first loaded; nothing to fetch or re-hash.
        try:
            # External await: a local read of the resident Basket, milliseconds; a missing blob
            # is refused, never waited for.
            package = await self._source.bytes_for(entry.package_sha256)
        except PackageUnavailableError as exc:
            return await self._refused(invocation, "package_unavailable", str(exc))
        # The channel that delivered the bytes is never trusted: only the hash decides.
        if sha256_of(package) != entry.package_sha256:
            return await self._refused(
                invocation, "hash_mismatch", "the package bytes do not match the entry's hash."
            )
        try:
            run = await self._loader.load(ctx.session, ctx.worker_id, entry, package)
        except ToolLoadError as exc:
            return await self._refused(invocation, exc.reason, str(exc))
        await _record(
            ctx, loaded_event(_identity(ctx), ctx.clock, entry.tool_id, _package_of(entry))
        )
        return run

    async def _refused(self, invocation: ToolInvocation, code: str, detail: str) -> str:
        """Record `tool.load_refused` and return the line the model reads."""
        ctx, entry = invocation.ctx, self._entry
        why = (entry.name, code, detail)
        await _record(ctx, load_refused_event(_identity(ctx), ctx.clock, entry.tool_id, why))
        return _line(code, detail)

    async def _execute(
        self, run: ToolCallable, invocation: ToolInvocation, arguments: JsonObject
    ) -> ToolOutput:
        """Call the tool under the timeout and turn its return value into a bounded result."""
        # A copy, so a tool that edits its arguments cannot change what the executor recorded.
        given: JsonObject = copy.deepcopy(arguments)
        try:
            async with asyncio.timeout(COMB_TOOL_TIMEOUT_S):
                value = await run(invocation.ctx.session, given)
        except TimeoutError:
            return _failure("timeout", f"{self._entry.name!r} ran past {COMB_TOOL_TIMEOUT_S:g}s.")
        except WorkerError:
            raise  # A Worker control exception (cancellation, a handoff) is never the tool's.
        except Exception as exc:
            # SAFETY: model-written code that passed the Quarantine Comb runs here; whatever it
            # raises becomes a result the model can read, not an error in the Worker's own loop.
            detail = f"{type(exc).__name__}: {str(exc)[:MAX_ERROR_DETAIL_CHARS]}"
            return _failure("failed", f"{self._entry.name!r} raised {detail}")
        return _render(value, self._entry.name)


def _render(value: object, name: str) -> ToolOutput:
    """Serialise a tool's return value as JSON text, truncated at the result bound."""
    try:
        text = json.dumps(value, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError, RecursionError):
        return _failure("result_unserialisable", f"{name!r} returned something that is not JSON.")
    if len(text) > MAX_COMB_RESULT_CHARS:
        text = text[:MAX_COMB_RESULT_CHARS] + TRUNCATION_MARKER
    return ToolOutput(text=text)


def _fits_cell(entry: CombToolEntry, ctx: WorkerContext) -> bool:
    """Return whether this Cell's bees may be offered `entry`: its OS fits and its scope does."""
    if not _supports_os(entry, ctx):
        return False
    return entry.scope is ToolScope.HIVE or entry.cell_id == ctx.cell.id


def _supports_os(entry: CombToolEntry, ctx: WorkerContext) -> bool:
    """Return whether the Cell's OS is one the tool was proved for (compared by wire value)."""
    return ctx.cell.capabilities.os.value in {os.value for os in entry.supported_os}


def _identity(ctx: WorkerContext) -> ToolIdentity:
    """Build the trail identity a Worker's tool events carry: this Worker is the actor."""
    return ToolIdentity(ctx.identity.hive_id, ctx.identity.node_id, str(ctx.worker_id))


def _package_of(entry: CombToolEntry) -> tuple[str, str, str]:
    """Return `(name, version, sha256)` for a `tool.loaded` event."""
    return (entry.name, entry.version, entry.package_sha256)


async def _record(ctx: WorkerContext, event: PheromoneEvent) -> None:
    """Record one event on the Worker's trail (local, bounded by its busy timeout)."""
    await ctx.trail.record(event)


def _line(code: str, detail: str) -> str:
    """Render a refusal the model reads: a stable code, the reason, and that nothing ran."""
    return f"hive.tool.{code}: {detail} Nothing was run."


def _failure(code: str, detail: str) -> ToolOutput:
    """Wrap a failed run as an error result."""
    return ToolOutput(text=f"hive.tool.{code}: {detail}", is_error=True)
