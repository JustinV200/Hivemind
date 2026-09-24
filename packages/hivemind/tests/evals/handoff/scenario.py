"""Run the two-bee Handoff scenario roadmap step 4.5 grades: checkpoint mid-task, resume, finish.

A first Drone attempt ("bee 1") writes two of four scratch files, then the runtime's own handoff
mechanism -- `hivemind.workers.telemetry.TelemetryTracker.handoff_requested`, the exact flag
`hivemind.workers.runtime.attempt.AttemptManager.request_handoff` sets from a real
`Intervene(Handoff)` (`hivemind.supervision.intervention.Handoff`, wire action
`InterventionAction.HANDOFF`) -- fires between tool calls, so `hivemind.workers.roles.drone.
outcome.HandoffRequestedError` stops it before the third write lands
(`hivemind.workers.roles.drone.role.Drone.run` is this suite's one entry point, called directly
rather than through the full async `hivemind.workers.runtime.loop.WorkerRuntime`: driving the real
tick loop's own `Intervene` race is exactly what `tests.e2e.kernel_helpers`' own module docstring
documents as non-deterministic for this same trigger, and calling `Drone.run` directly reaches the
identical checkpoint code -- `HandoffRequestedError`, `build_handoff_outcome`,
`hivemind.memory.write_checkpoint` -- deterministically instead). The resulting Handoff is stored
through `write_checkpoint` and re-read through `read_handoff`, exactly as `WorkerRuntime.
_handle_assign` does for a real `TaskAssign.resume_from`; a brand-new `WorkerContext` and `Drone`
("bee 2") -- a fresh worker id, a fresh `TelemetryTracker`, no in-process link to bee 1 at all --
is then handed only that stored `HandoffRef` and finishes the remaining two files.

Bee 1's own Handoff is stored and read back completely unmodified: `hivemind.workers.roles.drone.
outcome.build_handoff_outcome` derives `do_not_redo` (every side-effecting call that landed),
`tried_and_failed`, `constraints`, `open_threads` and `pinned_facts` from what the attempt actually
did (a defect this eval used to work around here, fixed at the source instead -- see git history
for the harness's own former `_augment_do_not_redo`), so grading roadmap step 4.5's "not repeating
do-not-redo steps" needs no help from this harness at all.

Fits into the Hive:
    Test infrastructure (codingrules section 14.5), not shipped. Used by
    `tests.evals.handoff.test_handoff_eval` (the fake-provider run) and `tests.evals.handoff.
    test_handoff_eval_live` (the `live_llm`/`local_llm` variants).

Key invariants:
    - Bee 1 and bee 2 share one `FakeSession` (the same Cell, matching a real resume) and one
      `InMemoryMemoryStore` (so bee 2's own `read_handoff` call finds what bee 1's own
      `write_checkpoint` call wrote), bundled in one `_World`; but they never share a
      `WorkerContext`, a `Drone` instance, a `TelemetryTracker` or a worker id: bee 2's only
      knowledge of bee 1's work is the `HandoffRef` it is assigned and resolves itself.
    - `_RecordingSession.written_paths` grows across both attempts in call order, so a caller
      slices it at the point bee 1 stopped to read only bee 2's own writes -- "the tool calls the
      fake session recorded" the roadmap step's own no-redo grading is measured from.

See Also:
    - .claude/roadmap.md step 4.5 for this eval's own exit condition.
    - tests.e2e.kernel_helpers for `tool_round_count`/`tool_response`/`write_call`/`text_response`,
      reused here rather than duplicated.
    - hivemind.memory.checkpoint for `write_checkpoint`/`read_handoff`, the two calls this module
      makes directly, matching `hivemind.workers.runtime`'s own use of them.
    - tests.evals.handoff.grader for the pure grading functions this scenario's result feeds.
"""

from __future__ import annotations

import dataclasses
from collections.abc import AsyncIterator, Mapping, Sequence
from pathlib import Path

from builders.llm import make_bound
from builders.workers import make_assignment, make_context
from e2e.kernel_helpers import text_response, tool_response, tool_round_count, write_call

from hivemind.cell import HoneyClearance
from hivemind.cell.fake import FakeSession
from hivemind.forage.slots import ModelSlot
from hivemind.llm import (
    LLMChunk,
    LLMProvider,
    LLMRequest,
    LLMResponse,
    ProviderCapabilities,
    ProviderHealth,
)
from hivemind.llm.fake import FakeLLMProvider
from hivemind.llm.slots import BoundModel
from hivemind.manifest import load_manifest
from hivemind.memory import (
    Handoff,
    InMemoryMemoryStore,
    MemoryContext,
    read_handoff,
    write_checkpoint,
)
from hivemind.pheromone.trail.memory import MemoryPheromoneTrail
from hivemind.workers.context import WorkerContext
from hivemind.workers.roles.drone import Drone
from hivemind.workers.telemetry import TelemetryTracker
from waggle.clock import Clock, FakeClock, SystemClock
from waggle.ids import TaskId
from waggle.messages import HandoffRef
from waggle.messages.task import TaskAssign

# Four distinct, independently checkable effects (roadmap step 4.5: "at least four tool steps
# with distinct, checkable effects"): one scratch file each, two per bee.
STEP_FILES: tuple[str, str, str, str] = ("step_1.txt", "step_2.txt", "step_3.txt", "step_4.txt")

# How many provider turns a live/local bee gets before this harness forces its checkpoint; see
# run_live_handoff_scenario's own docstring for why this is a best-effort nudge, not a guarantee,
# against a real model's own turn-taking.
_LIVE_TRIGGER_AFTER_CALLS = 2

__all__ = [
    "STEP_FILES",
    "HandoffScenarioResult",
    "run_fake_handoff_scenario",
    "run_live_handoff_scenario",
]


@dataclasses.dataclass(frozen=True, slots=True)
class HandoffScenarioResult:
    """Everything `tests.evals.handoff.grader.build_report` needs to grade one scenario run.

    Attributes:
        handoff: The Handoff bee 2 itself read back (`read_handoff`) and resumed from -- bee 1's
            own real output, unmodified (module docstring).
        expected_files: Every scratch path the whole task (both bees) was meant to produce.
        present_files: The subset of `expected_files` that actually exist once bee 2 finishes.
        second_bee_writes: Every path bee 2's own attempt committed, in call order -- "the tool
            calls the fake session recorded" roadmap step 4.5's no-redo grading measures from.
    """

    handoff: Handoff
    expected_files: tuple[str, ...]
    present_files: tuple[str, ...]
    second_bee_writes: tuple[str, ...]


@dataclasses.dataclass(frozen=True, slots=True)
class _World:
    """The Cell (session) and durable store both bees in one run share (module docstring)."""

    clock: Clock
    trail: MemoryPheromoneTrail
    store: InMemoryMemoryStore
    session: _RecordingSession


class _RecordingSession(FakeSession):
    """A FakeSession that also remembers every path `put_file` actually committed, in order.

    `hivemind.supervision.capping.apply` calls `session.put_file` only once a proposal is
    ACCEPTED (module docstring's "the tool calls the fake session recorded"): recording here,
    not in the Drone's own tool loop, is what lets this eval grade against landed effects rather
    than merely-proposed ones.
    """

    def __init__(self, scratch_dir: Path, clock: Clock) -> None:
        """Build an empty recorder scoped to `scratch_dir`."""
        super().__init__(scratch_dir=scratch_dir, clock=clock)
        self.written_paths: list[str] = []

    async def put_file(self, path: Path, data: bytes) -> None:
        """Delegate to FakeSession, then append `path` (posix-relative) to `written_paths`."""
        await super().put_file(path, data)
        self.written_paths.append(path.as_posix())


def _new_world(clock: Clock) -> _World:
    """Build a fresh `_World` over `clock`: an empty trail, memory store and recording session."""
    trail = MemoryPheromoneTrail(clock)
    return _World(
        clock=clock,
        trail=trail,
        store=InMemoryMemoryStore(trail),
        session=_RecordingSession(scratch_dir=Path("scratch"), clock=clock),
    )


async def run_fake_handoff_scenario(*, bad_second_bee: bool = False) -> HandoffScenarioResult:
    """Run the whole two-bee scenario over a FakeLLMProvider and return its gradeable result.

    Args:
        bad_second_bee: When True, bee 2's script deliberately rewrites `STEP_FILES[0]` -- a step
            the Handoff's own `do_not_redo` names -- instead of resuming forward. Roadmap step
            4.5's own "prove the grader has teeth" requirement: `tests.evals.handoff.
            test_handoff_eval` asserts this variant's no-redo grade fails.

    Returns:
        A HandoffScenarioResult ready for `tests.evals.handoff.grader.build_report`.
    """
    world = _new_world(FakeClock())

    handoff_ref, bee1_write_count = await _run_bee_one(world)
    handoff, second_bee_writes = await _run_bee_two(
        world, handoff_ref, bee1_write_count, bad_second_bee=bad_second_bee
    )

    present = await _present_files(world.session, STEP_FILES)
    return HandoffScenarioResult(
        handoff=handoff,
        expected_files=STEP_FILES,
        present_files=present,
        second_bee_writes=second_bee_writes,
    )


async def _run_bee_one(world: _World) -> tuple[HandoffRef, int]:
    """Run bee 1 to its checkpoint; return the ref it was stored under and its own write count."""
    # A mutable holder, not a direct closure over ctx1: the FakeLLMProvider's responder must be
    # supplied at construction time, before ctx1 (which needs that very provider) exists (the
    # same "hive_holder" idiom tests.e2e.kernel_helpers' own scenario (e) uses for the same
    # reason).
    holder: dict[str, TelemetryTracker] = {}

    def worker_turn(request: LLMRequest) -> LLMResponse:
        count = tool_round_count(request)
        if count == 0:
            return tool_response(request, (write_call(STEP_FILES[0]),))
        if count == 1:
            return tool_response(request, (write_call(STEP_FILES[1]),))
        # Two steps already landed: flip the same public flag a real Intervene(Handoff) would
        # (module docstring). The write_call below never actually lands -- _RecordingExecutor
        # checks handoff_requested before running it -- so bee 1 stops mid-task after step two.
        holder["telemetry"].handoff_requested = True
        return tool_response(request, (write_call(STEP_FILES[2]),))

    provider = FakeLLMProvider(responder=worker_turn)
    ctx1 = make_context(
        clock=world.clock,
        session=world.session,
        trail=world.trail,
        memory=world.store,
        bound=make_bound(provider=provider),
    )
    holder["telemetry"] = ctx1.telemetry
    assignment1 = make_assignment(clock=world.clock, objective="Write four step files to scratch.")

    outcome1 = await Drone().run(ctx1, assignment1, resume_from=None)
    # WorkerOutcome's own validator guarantees handoff is set whenever claimed is False
    # (hivemind.workers.base.WorkerOutcome._claimed_xor_handoff); this narrows the type for mypy.
    assert outcome1.claimed is False and outcome1.handoff is not None

    return await _checkpoint_bee_one(world, ctx1, assignment1.task_id, outcome1.handoff)


async def _checkpoint_bee_one(
    world: _World, ctx1: WorkerContext, task_id: TaskId | None, handoff: Handoff
) -> tuple[HandoffRef, int]:
    """Store bee 1's own Handoff, unmodified; return its ref and bee 1's own write count."""
    bee1_write_count = len(world.session.written_paths)
    memory_ctx = MemoryContext(store=ctx1.memory, identity=ctx1.identity, clock=ctx1.clock)
    handoff_ref = await write_checkpoint(handoff, task_id, memory_ctx)
    return handoff_ref, bee1_write_count


def _second_bee_good_turn(request: LLMRequest) -> LLMResponse:
    """Bee 2's honest script: write the two remaining files, then stop."""
    count = tool_round_count(request)
    if count == 0:
        return tool_response(request, (write_call(STEP_FILES[2]),))
    if count == 1:
        return tool_response(request, (write_call(STEP_FILES[3]),))
    return text_response("All four step files written.")


def _second_bee_bad_turn(request: LLMRequest) -> LLMResponse:
    """Bee 2's deliberately bad script: redo a do-not-redo'd step first (the negative case)."""
    count = tool_round_count(request)
    if count == 0:
        return tool_response(request, (write_call(STEP_FILES[0]),))
    if count == 1:
        return tool_response(request, (write_call(STEP_FILES[2]),))
    if count == 2:
        return tool_response(request, (write_call(STEP_FILES[3]),))
    return text_response("Done, redundantly.")


async def _run_bee_two(
    world: _World, handoff_ref: HandoffRef, bee1_write_count: int, *, bad_second_bee: bool
) -> tuple[Handoff, tuple[str, ...]]:
    """Run a brand-new Worker/Drone from `handoff_ref` alone; return what it resumed and wrote."""
    turn = _second_bee_bad_turn if bad_second_bee else _second_bee_good_turn
    provider = FakeLLMProvider(responder=turn)
    # A fresh WorkerContext: make_context mints a new worker id and a new TelemetryTracker on
    # every call (no override needed), and only session/trail/memory are shared -- the same Cell
    # and durable store a real resume would still see (module docstring).
    ctx2 = make_context(
        clock=world.clock,
        session=world.session,
        trail=world.trail,
        memory=world.store,
        bound=make_bound(provider=provider),
    )
    assignment2 = make_assignment(
        clock=world.clock,
        objective="Resume the task from the attached Handoff.",
        resume_from=handoff_ref,
    )
    resume_from = await _resolve_resume_from(ctx2, assignment2)
    await Drone().run(ctx2, assignment2, resume_from)
    return resume_from, tuple(world.session.written_paths[bee1_write_count:])


async def _resolve_resume_from(ctx: WorkerContext, assignment: TaskAssign) -> Handoff:
    """Mirror `WorkerRuntime._handle_assign`: resolve `resume_from` via `read_handoff`.

    Args:
        ctx: The fresh bee's own WorkerContext.
        assignment: The fresh bee's own TaskAssign; `resume_from` must already be set.
    """
    allowance = HoneyClearance.from_wire(assignment.clearance)
    ref = assignment.resume_from
    assert ref is not None  # every caller of this helper sets resume_from on its own assignment.
    return await read_handoff(ctx.memory, ref, allowance)


async def run_live_handoff_scenario(
    manifest_path: Path, environ: Mapping[str, str]
) -> HandoffScenarioResult:
    """Run the same two-bee scenario against a real provider resolved from `manifest_path`.

    Unlike `run_fake_handoff_scenario`, a real model's own tool calls cannot be scripted: this
    forces the checkpoint by wrapping the resolved provider in `_CheckpointAfterNCalls` instead
    of flipping `handoff_requested` from a scripted round, and asks for exact file names in the
    objective text rather than dictating tool calls directly. `tests.evals.handoff.
    test_handoff_eval_live` grades completion, Handoff shape and no-redo, all strictly:
    `hivemind.workers.roles.drone.sources.DroneSources.handoff` now surfaces the whole resumed
    Handoff -- `do_not_redo` included, rendered as an explicit instruction list -- into bee 2's own
    prompt (defect 2 this dispatch fixed), so a real model has the same textual signal the fake
    scenario's own scripting only used to approximate.

    Args:
        manifest_path: A Hive Manifest naming a real `[llm.slots.worker]` provider
            (`docs/manifests/minimal.toml` or `docs/manifests/local.toml`).
        environ: Where provider API keys are read from (codingrules section 13); the caller's own
            `os.environ`, never read by this module directly.

    Returns:
        A HandoffScenarioResult ready for `tests.evals.handoff.grader.build_report`.
    """
    # Imported here, not at module level: hivemind.cli pulls in more of the composition root than
    # the fake-only path above needs, and this function is the only caller.
    from hivemind.cli.stores import build_registry

    clock = SystemClock()  # A real provider call has real latency; nothing here fakes time.
    manifest = load_manifest(manifest_path)
    registry = build_registry(manifest, environ, clock)
    bound = registry.bound(ModelSlot.WORKER)
    world = _new_world(clock)

    handoff_ref, bee1_write_count = await _run_live_bee_one(world, bound)
    handoff, second_bee_writes = await _run_live_bee_two(
        world, bound, handoff_ref, bee1_write_count
    )

    present = await _present_files(world.session, STEP_FILES)
    return HandoffScenarioResult(
        handoff=handoff,
        expected_files=STEP_FILES,
        present_files=present,
        second_bee_writes=second_bee_writes,
    )


async def _run_live_bee_one(world: _World, bound: BoundModel) -> tuple[HandoffRef, int]:
    """Run bee 1 against a real provider, forcing its checkpoint via `_CheckpointAfterNCalls`."""
    telemetry1 = TelemetryTracker(context_window=bound.context_window)
    wrapped1 = _CheckpointAfterNCalls(bound.provider, telemetry1, _LIVE_TRIGGER_AFTER_CALLS)
    ctx1 = make_context(
        clock=world.clock,
        session=world.session,
        trail=world.trail,
        memory=world.store,
        bound=dataclasses.replace(bound, provider=wrapped1),
        telemetry=telemetry1,
    )
    assignment1 = make_assignment(
        clock=world.clock,
        objective=(
            f"Write two short scratch files: {STEP_FILES[0]} first (one write_file call), then "
            f"{STEP_FILES[1]} (a second, separate write_file call). Any short content is fine."
        ),
    )

    outcome1 = await Drone().run(ctx1, assignment1, resume_from=None)
    assert outcome1.claimed is False and outcome1.handoff is not None
    return await _checkpoint_bee_one(world, ctx1, assignment1.task_id, outcome1.handoff)


async def _run_live_bee_two(
    world: _World, bound: BoundModel, handoff_ref: HandoffRef, bee1_write_count: int
) -> tuple[Handoff, tuple[str, ...]]:
    """Run a brand-new Worker/Drone against the same real provider, resuming from `handoff_ref`."""
    ctx2 = make_context(
        clock=world.clock, session=world.session, trail=world.trail, memory=world.store, bound=bound
    )
    assignment2 = make_assignment(
        clock=world.clock,
        objective=(
            f"Resume the task from the attached Handoff. Write the two remaining scratch files: "
            f"{STEP_FILES[2]} then {STEP_FILES[3]}. Do not rewrite any file the Handoff's "
            f"do-not-redo notes already list."
        ),
        resume_from=handoff_ref,
    )
    resume_from = await _resolve_resume_from(ctx2, assignment2)
    await Drone().run(ctx2, assignment2, resume_from)
    return resume_from, tuple(world.session.written_paths[bee1_write_count:])


class _CheckpointAfterNCalls:
    """Wrap any LLMProvider, flipping a TelemetryTracker's `handoff_requested` after N calls.

    A real provider's own tool choices are not scriptable (unlike FakeLLMProvider): this is how
    `run_live_handoff_scenario` simulates the same runtime handoff mechanism
    `run_fake_handoff_scenario` triggers directly -- the wrapped provider still answers for real,
    this only counts calls and flips the same public flag `hivemind.workers.runtime.attempt.
    AttemptManager.request_handoff` sets from a real Intervene(Handoff).
    """

    def __init__(self, inner: LLMProvider, telemetry: TelemetryTracker, trigger_after: int) -> None:
        """Wrap `inner`, triggering a checkpoint after `trigger_after` complete/stream calls.

        Args:
            inner: The real provider to delegate every call to, unchanged.
            telemetry: The attempt's own tracker; `handoff_requested` is set on it, in place.
            trigger_after: How many completed calls before the flag flips.
        """
        self._inner = inner
        self._telemetry = telemetry
        self._trigger_after = trigger_after
        self._calls = 0

    @property
    def name(self) -> str:
        """This provider's manifest name; delegated to the wrapped instance."""
        return self._inner.name

    @property
    def capabilities(self) -> ProviderCapabilities:
        """This provider's declared capabilities; delegated to the wrapped instance."""
        return self._inner.capabilities

    async def complete(self, request: LLMRequest) -> LLMResponse:
        """Delegate to `inner.complete`, then count this call toward the trigger."""
        response = await self._inner.complete(request)
        self._note_call()
        return response

    async def stream(self, request: LLMRequest) -> AsyncIterator[LLMChunk]:
        """Delegate to `inner.stream`, then count this call toward the trigger."""
        async for chunk in self._inner.stream(request):
            yield chunk
        self._note_call()

    async def count_tokens(self, request: LLMRequest) -> int | None:
        """Delegate to `inner.count_tokens`; never counted toward the trigger."""
        return await self._inner.count_tokens(request)

    async def health(self) -> ProviderHealth:
        """Delegate to `inner.health`; never counted toward the trigger."""
        return await self._inner.health()

    async def aclose(self) -> None:
        """Delegate to `inner.aclose`: the wrapped provider owns every connection."""
        await self._inner.aclose()

    def _note_call(self) -> None:
        """Count one landed call, flipping `handoff_requested` once `trigger_after` is reached."""
        self._calls += 1
        if self._calls >= self._trigger_after:
            self._telemetry.handoff_requested = True


async def _present_files(session: _RecordingSession, expected: Sequence[str]) -> tuple[str, ...]:
    """Return the subset of `expected` that actually exists in `session`'s in-memory scratch.

    An explicit loop, not a generator expression with `await` in its filter clause: the latter
    compiles to an async generator (PEP 530), which `tuple()` cannot consume directly.
    """
    present: list[str] = []
    for name in expected:
        try:
            await session.get_file(Path(name))
        except FileNotFoundError:
            continue
        present.append(name)
    return tuple(present)
