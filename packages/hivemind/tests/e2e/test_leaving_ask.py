"""End-to-end: a declared leaving marked ASK blocks the task until `hive inbox answer` resumes it.

Roadmap steps 5.0c/5.0d, the suite's own "Tests" section: "one end-to-end scenario... where a
declared leaving marked ASK blocks until answered through the same path `hive inbox answer` uses."
Built the same way as `tests.e2e.test_kernel_on_hive_stand`'s own scenario (d) (a real `hive
run`-shaped `Hive`, a real SQLite-backed Brood Chamber, `hive inbox`/`hive inbox answer` driven for
real through `typer.testing.CliRunner`), because that is the one composition root
(`hivemind.cli.compose.build_hive`) whose Brood Chamber a separate `hive inbox answer` process can
actually reach by `--manifest` path (`hivemind.cli.compose.build_hive` always opens real, on-disk
SQLite stores; `tests.e2e.kernel_helpers.build_cluster_pair`'s own in-memory chamber has no
manifest a CLI invocation could ever open).

Roadmap step 5.0c's own suggested default table only ever answers ASK for a path a plan declared
(roadmap 5.0b's hard rule) that is either executable outside `keep_root`, a startup/system
location, or an over-threshold home file -- never a plain small file under home on the Hive Stand
itself (that is ALLOW, no question asked). This scenario uses an executable-suffixed target
(`.exe` on Windows, `.sh` elsewhere) under the operator's own home directory, so the ASK verdict is
deterministic on any host `deterministic_checks()` runs on, without depending on the shipped
`max_home_bytes` threshold.

No existing composition root lets a Worker's own `write_file` tool call reach outside scratch: an
outside-scratch write needs both an `fs:write` capability `hivemind.workers.capabilities.
worker_capabilities` never grants beyond `scratch_root`, and a lease `hivemind.cell.RealCellLease.
allowed_paths` never includes beyond scratch either, whatever a task declares in `leaves`
(confirmed against `tests.e2e.test_kernel_on_hive_stand`'s own scenario (f), which proves exactly
that capability gap for an *undeclared* path) -- closing both is roadmap step 5.0e's own `keep`
tool, not this dispatch's. This scenario's own `_LeavingWorker` stands in for that still-missing
tool: it builds its `OUTSIDE_SCRATCH_WRITE` `Proposal` by hand and calls `ctx.capping.propose`/
`.run` directly with an explicit `fs:write` capability and a `_WideningLeaseView` (wraps the real
`ctx.lease`, widening only `is_path_allowed` for its own declared target; every other call,
`note_restore_path` included, still reaches the real lease, so persistence and the Leavings
ledger behave exactly as they would once 5.0e's own capability/allowlist grant exists) for its own
declared target -- exactly the same `CappingGate`, `GateDeps` (built for real by `hivemind.
wardens.spawn.spawn._build_capping_gate` from this task's own `TaskAssign.leaves`) and `ctx.asker`
(the real transport-backed `Mailbox`, already substituted in by `WorkerRuntime.__init__` before
any role's `run` is ever called) a real Drone's `write_file` tool would use once 5.0e exists.
Swapped in for the Hive Stand's own `hivemind.workers.roles.drone.Drone` by patching `hive.
warden._deps.worker_factory` after `build_hive` returns and before `run_hive` starts the Warden
(`hivemind.wardens.warden.Warden` holds `_deps` as a plain, mutable instance attribute;
`WardenDeps` itself is a frozen dataclass, so `dataclasses.replace` builds the patched copy) --
nothing under `hivemind/` is changed to make this scenario possible.

Fits into the Hive:
    Test infrastructure (codingrules section 14.2), not shipped.

Key invariants:
    - None: this module holds tests only.

See Also:
    - .claude/roadmap.md steps 5.0c/5.0d for this scenario's own requirement, verbatim.
    - tests.e2e.test_kernel_on_hive_stand for scenario (d) (the `hive inbox answer` pattern this
      module reuses) and scenario (f) (the outside-scratch capability gap this module's own
      `_LeavingWorker` stands in for).
    - hivemind.supervision.capping.checks.human for HumanCheck, the HUMAN rung this scenario
      proves end to end, including the `Answer.chosen_option` roundtrip through a real `hive
      inbox answer --option` -> `Note` -> `sync_answers_from_chamber` -> wire `Answer` hop.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import os
from pathlib import Path

import pytest
from builders.cli import fake_manifest
from builders.workers import make_outcome
from e2e.kernel_helpers import HaikuScript, text_response, wait_until
from typer.testing import CliRunner

from hivemind.brood_chamber import TaskFilter, TaskStatus
from hivemind.cell import HoneyClearance
from hivemind.cell.leavings import ApprovedBy
from hivemind.cli.app import app
from hivemind.cli.compose import Hive, build_hive, run_goal, run_hive
from hivemind.forage.tempo import Tempo
from hivemind.guard import CapabilitySet
from hivemind.llm import LLMRequest, LLMResponse
from hivemind.manifest import load_manifest
from hivemind.memory import Handoff
from hivemind.pheromone import TrailQuery
from hivemind.supervision.capping import Proposal, ProposalState, RiskTier
from hivemind.supervision.capping.lease_view import LeaseView
from hivemind.workers.base import WorkerOutcome
from hivemind.workers.context import WorkerContext
from waggle.clock import SystemClock
from waggle.ids import new_message_id
from waggle.messages.capping import ActionKind, ProposedAction
from waggle.messages.labels import Postcondition, PostconditionKind
from waggle.messages.task import TaskAssign, WorkerRole

pytestmark = pytest.mark.e2e

_GOAL = "install a licence checker that must stay on this machine"
_TIMEOUT_S = 10.0
_EXECUTABLE_SUFFIX = ".exe" if os.name == "nt" else ".sh"
_TARGET = str(Path.home() / f"hivemind_e2e_leaving{_EXECUTABLE_SUFFIX}")
_LEAVE_REASON = "the goal itself asks for the checker to remain installed"

runner = CliRunner()


def _leaving_plan() -> dict[str, object]:
    """A single-task plan declaring `_TARGET` as a leaving, with acceptance on the same path."""
    return {
        "tasks": [
            {
                "key": "install",
                "title": "Install the checker",
                "objective": f"Leave the checker installed at {_TARGET}.",
                "acceptance": [
                    {"kind": "FILE_EXISTS", "subject": _TARGET, "argv": [], "expected": None}
                ],
                "needs": {},
                "clearance": "C1",
                "depends_on": [],
                "leaves": [{"pattern": _TARGET, "reason": _LEAVE_REASON}],
            }
        ]
    }


class _WideningLeaseView:
    """Wrap a real `LeaseView`, allowing one extra path (module docstring's own explanation).

    Every call but `is_path_allowed` reaches the real lease unchanged, so restore bookkeeping,
    persistence and the Leavings ledger all behave exactly as they would through a real
    capability/allowlist grant (roadmap step 5.0e's own future job).
    """

    def __init__(self, inner: LeaseView, extra_allowed: Path) -> None:
        """Wrap `inner`, additionally allowing `extra_allowed`."""
        self._inner = inner
        self._extra = extra_allowed.resolve(strict=False)

    @property
    def scratch_root(self) -> Path:
        """Delegates to the wrapped lease."""
        return self._inner.scratch_root

    @property
    def allowed_paths(self) -> tuple[Path, ...]:
        """The wrapped lease's own allowed paths, plus `extra_allowed`."""
        return (*self._inner.allowed_paths, self._extra)

    def is_path_allowed(self, path: Path) -> bool:
        """True for `extra_allowed`, or whatever the wrapped lease already allows."""
        return path == self._extra or self._inner.is_path_allowed(path)

    async def note_touched_path(self, path: Path) -> None:
        """Delegates to the wrapped lease."""
        await self._inner.note_touched_path(path)

    def note_restore_path(
        self,
        path: Path,
        prior: bytes | None,
        *,
        persist: bool = False,
        approved_by: ApprovedBy | None = None,
        reason: str | None = None,
    ) -> None:
        """Delegates to the wrapped lease, so persistence and the Leavings ledger work for real."""
        self._inner.note_restore_path(
            path, prior, persist=persist, approved_by=approved_by, reason=reason
        )


class _LeavingWorker:
    """Stand in for roadmap step 5.0e's own `keep` tool: propose `_TARGET` as a leaving.

    Proposes `_TARGET` as an `OUTSIDE_SCRATCH_WRITE`, directly through the real `CappingGate`
    this task's own Warden built, with an explicit `fs:write` capability for `_TARGET` -- no
    other composition root lets a Worker reach outside scratch at all (module docstring's own
    scenario-(f) cross-reference).
    """

    @property
    def role(self) -> WorkerRole:
        """Reports DRONE, the role `_leaving_plan`'s own task is assigned under."""
        return WorkerRole.DRONE

    async def run(
        self, ctx: WorkerContext, assignment: TaskAssign, resume_from: Handoff | None
    ) -> WorkerOutcome:
        """Propose and run the leaving write; the task blocks here until the ASK is answered."""
        action = ProposedAction(
            kind=ActionKind.DIFF,
            summary="Install the licence checker.",
            diff="@@ -0,0 +1,1 @@\n+checker\n",
            diff_sha256=None,
            command=(),
            cwd=None,
            paths=(_TARGET,),
            steps=(),
        )
        postconditions = (
            Postcondition(
                kind=PostconditionKind.FILE_EXISTS, subject=_TARGET, argv=(), expected=None
            ),
        )
        proposal = Proposal(
            id=new_message_id(ctx.clock),
            task_id=assignment.task_id,
            cell_id=ctx.cell.id,
            proposer=ctx.worker_id,
            risk_tier=RiskTier.OUTSIDE_SCRATCH_WRITE,
            action=action,
            postconditions=postconditions,
            tempo=Tempo.from_wire(assignment.tempo),
            spend_estimate_usd=0.0,
            clearance=HoneyClearance.from_wire(assignment.clearance),
            reason=_LEAVE_REASON,
            state=ProposalState.PROPOSED,
        )
        await ctx.capping.propose(proposal)
        # The stand-in capability/allowlist grant module docstring explains (roadmap 5.0e's own
        # future job): only this one declared path, never wider. The CellSession itself (shared
        # across every sub-bee on this Warden) does its own, separate reachability check with its
        # own fixed `_allowed_paths` snapshot taken at Warden.start() -- widening the lease alone
        # (above) is not enough, so this test-only patch widens the one session object too.
        target_path = Path(_TARGET)
        ctx.session._allowed_paths = (*ctx.session._allowed_paths, target_path)  # type: ignore[attr-defined]
        wide_caps = CapabilitySet.parse(f"fs:write:{target_path.as_posix()}")
        wide_lease = _WideningLeaseView(ctx.lease, target_path)
        # External await: blocks until the leave-policy ASK verdict is answered (roadmap 5.0d),
        # over the real Worker -> Warden -> Queen -> human chain; times out to discard past
        # GateDeps.human_timeout_s, never hangs the test forever.
        outcome = await ctx.capping.run(proposal.id, wide_caps, wide_lease, ctx.asker)
        assert outcome.state is ProposalState.VERIFIED, outcome.reason
        return make_outcome(summary="Installed the checker.")


def _worker_turn_unused(request: LLMRequest) -> LLMResponse:
    """The WORKER slot is never called: `_LeavingWorker` never awaits a model."""
    return text_response("unused")


def _set_full_access(manifest_path: Path) -> None:
    """Patch `[hive_stand] access_level = "FULL"` onto an already-written manifest.

    `builders.cli.fake_manifest` never writes this key (it defaults to SCRATCH), and roadmap
    step 5.0c's own hard rule denies every leaving unconditionally at SCRATCH -- mirrors
    `tests.e2e.kernel_helpers.set_budget_fraction`'s own after-the-fact text patch, for the same
    reason: no builder knob for it exists yet to extend instead.
    """
    text = manifest_path.read_text(encoding="utf-8")
    manifest_path.write_text(
        text.replace("[hive_stand]\n", '[hive_stand]\naccess_level = "FULL"\n', 1),
        encoding="utf-8",
    )


def _hive(manifest_path: Path) -> Hive:
    """Build a Hive scripted only for the QUEEN-slot plan call; the WORKER slot is never reached."""
    manifest = load_manifest(manifest_path, {})
    script = HaikuScript(_worker_turn_unused, plan=_leaving_plan())
    return build_hive(
        manifest, environ={}, clock=SystemClock(), responders={"fake": script.responder}
    )


async def _task_is_blocked(hive: Hive) -> bool:
    """Return whether the goal's own (only) task is currently BLOCKED on a question."""
    tasks = await hive.stores.chamber.list(TaskFilter())
    return bool(tasks) and tasks[0].status is TaskStatus.BLOCKED


def test_a_declared_leaving_marked_ask_blocks_until_hive_inbox_answer_resumes_it(
    tmp_path: Path,
) -> None:
    """A leave-policy ASK verdict raises a Question that only "keep" resumes.

    `hive inbox answer ... --option 0` (keep) resumes the task, and the file is left in place
    with `approved_by=HUMAN` on the trail.
    """
    manifest_path = fake_manifest(tmp_path, capabilities="full")
    _set_full_access(manifest_path)
    hive = _hive(manifest_path)
    # Swap in _LeavingWorker for this one Hive's Warden (module docstring: a test-only patch, no
    # hivemind/ source touched); WardenDeps is frozen, so this builds a new copy via replace.
    hive.warden._deps = dataclasses.replace(
        hive.warden._deps, worker_factory=lambda _role: _LeavingWorker()
    )
    target = Path(_TARGET)
    try:
        asyncio.run(_run_leaving_ask(hive, manifest_path))
    finally:
        target.unlink(missing_ok=True)


async def _run_leaving_ask(hive: Hive, manifest_path: Path) -> None:
    """Run the goal, answer its leave Question with "keep" through the CLI, and assert it landed."""
    async with run_hive(hive):
        goal_task = asyncio.ensure_future(
            run_goal(hive, _GOAL, clearance=HoneyClearance.C1, timeout_s=_TIMEOUT_S)
        )
        await wait_until(lambda: _task_is_blocked(hive), timeout_s=_TIMEOUT_S)
        listed = await asyncio.to_thread(
            runner.invoke, app, ["inbox", "--manifest", str(manifest_path), "--json"]
        )
        assert listed.exit_code == 0, listed.output
        question = json.loads(listed.output)["questions"][0]
        assert question["options"] == ["keep", "keep for this whole goal", "discard"]
        answered = await asyncio.to_thread(
            runner.invoke,
            app,
            [
                "inbox",
                "answer",
                question["id"],
                "keep",
                "--option",
                "0",
                "--manifest",
                str(manifest_path),
            ],
        )
        assert answered.exit_code == 0, answered.output
        report = await goal_task
    assert report.succeeded, report
    # A short, already-flushed file: reading it directly (not through a thread) is fine here,
    # the same way scenario (a) reads scratch files back synchronously after the goal completes.
    assert await asyncio.to_thread(Path(_TARGET).read_text, encoding="utf-8") == "checker"
    payloads = [
        event.payload
        for event in await hive.stores.trail.query(TrailQuery())
        if event.kind == "capping.leave_decided"
    ]
    assert payloads, "capping.leave_decided never appeared on the trail"
    assert payloads[0]["verdict"] == "ASK"
    assert payloads[0]["persisted"] is True
    assert payloads[0]["human_answer"] == "KEEP"
