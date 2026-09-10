"""Write a fake_manifest: a Hive Manifest TOML bound to one scripted `kind = "fake"` provider.

`fake_manifest` is what every roadmap step 3.21/3.22 test builds a `hivemind.cli.compose.Hive`
from: a real TOML file on disk (`hivemind.manifest.load_manifest` reads only real files, codingrules
section 9), with every `hivemind.forage.slots.ModelSlot` bound to one `[llm.providers.fake] kind =
"fake"` provider, so `hivemind.cli.compose.build_hive`'s own `responders` argument can script every
call the Queen, a Warden and a Drone ever make through one `hivemind.llm.FakeLLMProvider` instance.
Short `[supervision]`/`[queen]` intervals keep a FakeClock-driven e2e-shaped test's own pump loop
short; `[hive_stand] scratch_root` and `[hive] db`'s own directory are both created here (`hivemind.
cell.local.HiveStandSource.lease`'s own `shutil.disk_usage` call needs `scratch_root` to already
exist, and `sqlite3.connect` never creates a missing parent directory), so a caller never needs to
`mkdir` around this builder.

Fits into the Hive:
    Test infrastructure (codingrules section 14.5), not shipped. Used by every test under
    `packages/hivemind/tests/unit/cli` and by roadmap step 3.22's own e2e suite.

`pump_until_done` is the FakeClock-driven counterpart every such test's own polling loop needs:
`hivemind.cli.compose.run_hive`/`run_goal` run the Queen, a Warden and a sub-bee as three separate
asyncio tasks that only make progress at genuine suspension points, so advancing a shared FakeClock
once is not enough by itself -- the event loop also needs several scheduling turns (`await
asyncio.sleep(0)`) after each advance for a cascade (Warden tick -> spawn -> sub-bee tick -> a tool
loop round -> the Capping gate) to fully settle before the next `clock.sleep()` in the chain (a
poll interval, a heartbeat deadline) is even registered to advance past.

Fits into the Hive:
    Test infrastructure (codingrules section 14.5), not shipped. Used by every test under
    `packages/hivemind/tests/unit/cli` and by roadmap step 3.22's own e2e suite.

Key invariants:
    - Every model id and provider name here is neutral (`"test-model"`, `"fake"`), never a real
      vendor id (`scripts/check_no_model_ids.py`).
    - `capabilities="none"` writes exactly `hivemind.llm.ProviderCapabilities.none()`'s own field
      values as `[llm.providers.fake.capabilities]` overrides; `"full"` (the default) omits that
      section entirely, so `hivemind.llm.registry._build_fake`'s own base
      (`ProviderCapabilities.full()`) travels unchanged.
    - Every `hivemind.forage.slots.ModelSlot` resolves to the one `"fake"` provider
      (`hivemind.manifest.schema.llm.LlmSection`'s own validator requires this of any manifest).
    - `pump_until_done` never blocks forever: it gives up and raises `AssertionError` after
      `limit` clock advances, matching `builders.queen.WardenEnd.pump_until`'s own contract.
    - `worker_fallback=True` (roadmap step 3.22, scenario (c)) adds a second `[llm.slots.
      local_worker]` row, bound to the same `"fake"` provider under a distinct neutral model id,
      and sets `[llm.slots.worker] fallback = "local_worker"`, so `hivemind.queen.deps.QueenDeps.
      bindings` has somewhere for an escalated Alarm's own REBIND decision to name.
    - `handoff_threshold`, when given, writes `[memory] handoff_threshold`; omitted, the manifest
      carries no `[memory]` section at all and `hivemind.manifest.schema.supervision.
      MemorySection`'s own default applies.

See Also:
    - .claude/codingrules.md section 14.5 for the builders-over-fixtures rule this module follows.
    - hivemind.cli.compose.build_hive for `responders`, the seam this manifest's one provider is
      built to be scripted through.
    - hivemind.llm.capabilities for ProviderCapabilities.full/none, the two shapes `capabilities`
      selects between.
"""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from waggle.clock import Clock, FakeClock
from waggle.ids import new_hive_id, new_node_id

__all__ = ["fake_manifest", "pump_until_done"]

# pump_until_done's own cadence: a small clock step so a poll/heartbeat interval is crossed in a
# few pumps, and several real scheduling turns per step so a whole message cascade (module
# docstring) settles before the next step -- both tuned empirically against this suite's own
# three-haiku scenario, not derived from any manifest value.
_PUMP_STEP_S = 0.02
_PUMP_YIELDS_PER_STEP = 50
_PUMP_STEP_LIMIT = 2_000

_REPO_ROOT = Path(__file__).resolve().parents[4]
_POLICY_PATH = _REPO_ROOT / "docs" / "supervision" / "default-policy.toml"
_TIERS_PATH = _REPO_ROOT / "docs" / "supervision" / "capping-tiers.toml"

_MODEL_ID = "test-model"  # Neutral (codingrules 8.6); never a real vendor id.
_STRONG_MODEL_ID = "test-model-strong"  # worker_fallback=True's own second binding's model id.
_SOURCE_ID = "fake_default"  # The one [forage.map.<source_id>] entry every slot's binding prices.

# Every hivemind.forage.slots.ModelSlot's own lowercase manifest key (hivemind.manifest.schema.llm.
# LlmSection's own validator requires every one of these to resolve).
_SLOT_KEYS = (
    "queen",
    "attendant",
    "warden",
    "worker",
    "ripener",
    "scaffolder",
    "embedder",
    "judge",
    "transcriber",
)

# hivemind.llm.capabilities.ProviderCapabilities.none()'s own field values, written as
# [llm.providers.fake.capabilities] overrides for the zero-capability variant.
_NONE_CAPABILITIES = """
[llm.providers.fake.capabilities]
native_tool_calls = false
schema_output = false
json_mode = false
vision = false
streaming = false
reasoning_control = false
context_window = 8192
system_role = false
parallel_tool_calls = false
token_counting = false
"""


def fake_manifest(
    tmp_path: Path,
    *,
    capabilities: str = "full",
    clock: Clock | None = None,
    worker_fallback: bool = False,
    handoff_threshold: float | None = None,
) -> Path:
    """Write `<tmp_path>/hive.toml`: every ModelSlot bound to one `kind = "fake"` provider.

    Args:
        tmp_path: A test's own tmp_path. Data files land under `<tmp_path>/data`, scratch under
            `<tmp_path>/scratch`; both directories are created here.
        capabilities: `"full"` (default) for `ProviderCapabilities.full()`'s own shape, or
            `"none"` for `ProviderCapabilities.none()`'s own shape (module docstring).
        clock: Mints `[hive] id`/`node_id`; a fresh FakeClock when omitted.
        worker_fallback: When True, adds a second `[llm.slots.local_worker]` row and a `fallback`
            on `[llm.slots.worker]` naming it (module docstring's own "Key invariants" entry).
        handoff_threshold: When given, writes `[memory] handoff_threshold`; omitted writes no
            `[memory]` section at all.

    Returns:
        The written manifest's own path.
    """
    active_clock = clock if clock is not None else FakeClock()
    data_dir = tmp_path / "data"
    scratch_root = tmp_path / "scratch"
    data_dir.mkdir(parents=True, exist_ok=True)
    scratch_root.mkdir(parents=True, exist_ok=True)
    spec = _ManifestSpec(
        hive_id=new_hive_id(active_clock),
        node_id=new_node_id(active_clock),
        db_path=data_dir / "hive.sqlite3",
        scratch_root=scratch_root,
        capabilities=capabilities,
        worker_fallback=worker_fallback,
        handoff_threshold=handoff_threshold,
    )
    manifest_path = tmp_path / "hive.toml"
    manifest_path.write_text(_render(spec), encoding="utf-8")
    return manifest_path


@dataclass(frozen=True, slots=True)
class _ManifestSpec:
    """Every value `_render` needs, grouped to stay within codingrules 5.1's five-parameter cap."""

    hive_id: str
    node_id: str
    db_path: Path
    scratch_root: Path
    capabilities: str
    worker_fallback: bool
    handoff_threshold: float | None


def _render(spec: _ManifestSpec) -> str:
    """Render the manifest's full TOML text."""
    sections = [
        _hive_section(spec.hive_id, spec.node_id, spec.db_path),
        _queen_and_hive_stand_section(spec.scratch_root),
        _provider_section(spec.capabilities),
        _slots_section(worker_fallback=spec.worker_fallback),
        _forage_section(),
        _supervision_section(),
        _memory_section(spec.handoff_threshold),
    ]
    return "\n".join(sections)


def _hive_section(hive_id: str, node_id: str, db_path: Path) -> str:
    """Build the `[hive]` section: identity and the one SQLite file every store shares."""
    return (
        f'[hive]\nid = "{hive_id}"\nnode_id = "{node_id}"\nname = "Fake Test Hive"\n'
        f'db = "{db_path.as_posix()}"\n'
    )


def _queen_and_hive_stand_section(scratch_root: Path) -> str:
    """Build `[queen]` and `[hive_stand]`: short cadences, the Hive Stand enabled and scratched."""
    return (
        "[queen]\ntick_interval_s = 0.05\nheartbeat_interval_s = 0.05\n\n"
        f'[hive_stand]\nenabled = true\nscratch_root = "{scratch_root.as_posix()}"\n'
    )


def _provider_section(capabilities: str) -> str:
    """Build `[llm.providers.fake]`, with capability overrides only for `capabilities="none"`."""
    base = '[llm.providers.fake]\nkind = "fake"\n'
    return base + _NONE_CAPABILITIES if capabilities == "none" else base


def _slots_section(*, worker_fallback: bool) -> str:
    """Build one `[llm.slots.<slot>]` per ModelSlot, plus `local_worker` when `worker_fallback`."""
    rows = []
    for key in _SLOT_KEYS:
        fallback_line = 'fallback = "local_worker"\n' if worker_fallback and key == "worker" else ""
        rows.append(f'[llm.slots.{key}]\nprovider = "fake"\nmodel = "{_MODEL_ID}"\n{fallback_line}')
    if worker_fallback:
        rows.append(f'[llm.slots.local_worker]\nprovider = "fake"\nmodel = "{_STRONG_MODEL_ID}"\n')
    return "\n".join(rows)


def _memory_section(handoff_threshold: float | None) -> str:
    """Build `[memory]`, only when `handoff_threshold` was given (module docstring)."""
    if handoff_threshold is None:
        return ""
    return f"[memory]\nhandoff_threshold = {handoff_threshold}\n"


def _forage_section() -> str:
    """Build `[forage.map.<source>]` (priced, graded) and the required `[forage.roles.drone]`.

    `seats = 4` on the map entry (`ModelSourceSpec.seats` otherwise defaults to 1) matters more
    than it looks: `hivemind.forage.allocate.grant`'s own `reachable_seats` subtracts
    `RoyalReserve.seats` (default 1) from every source's free seats, so a map entry left at the
    default would always compute `max_sub_bees = 0` and no Drone would ever spawn.
    """
    return (
        f'[forage.map.{_SOURCE_ID}]\nprovider = "fake"\nmodel = "{_MODEL_ID}"\ngrade = 3\n'
        "context_window = 8192\nseats = 4\n\n"
        "[forage.roles.drone]\ncpu_cores = 0.5\nmemory_bytes = 268435456\n"
        "token_rate_per_minute = 20000\n"
    )


def _supervision_section() -> str:
    """Build `[supervision]`: the shipped policy/tiers files, and short heartbeat cadences."""
    return (
        "[supervision]\n"
        f'policy_file = "{_POLICY_PATH.as_posix()}"\n'
        f'capping_tiers_file = "{_TIERS_PATH.as_posix()}"\n'
        "heartbeat_interval_s = 0.05\n"
        "heartbeat_miss_limit = 3\n"
    )


async def pump_until_done[ResultT](
    clock: FakeClock, coro: Coroutine[Any, Any, ResultT], *, limit: int = _PUMP_STEP_LIMIT
) -> ResultT:
    """Drive `coro` to completion by repeatedly advancing `clock` and yielding to the loop.

    See the module docstring for why one `clock.advance()` per pump is not enough by itself: the
    Queen, a Warden and its sub-bees are separate asyncio tasks, and a whole message cascade
    between two `clock.sleep()`-gated waits needs several real scheduling turns to settle.

    Args:
        clock: The FakeClock every collaborator `coro` touches shares.
        coro: The coroutine to run to completion (typically `hivemind.cli.compose.run_goal`,
            already wrapped in whatever `async with run_hive(hive):` block it needs).
        limit: The most clock advances to make before giving up.

    Returns:
        `coro`'s own result, once it completes.

    Raises:
        AssertionError: `coro` was still not done after `limit` advances.
    """
    task = asyncio.ensure_future(coro)
    for _ in range(limit):
        if task.done():
            break
        clock.advance(_PUMP_STEP_S)
        # Checked after every single yield, not just once per step: breaking out the moment
        # `coro` finishes (rather than always burning the full `_PUMP_YIELDS_PER_STEP` turns)
        # is what keeps a passing run fast; the full burst only matters for a step that needs
        # every one of those turns to cascade a message all the way through.
        for _ in range(_PUMP_YIELDS_PER_STEP):
            if task.done():
                break
            await asyncio.sleep(0)
    if not task.done():
        task.cancel()
        raise AssertionError(f"pump_until_done gave up after {limit} clock advances.")
    return await task
