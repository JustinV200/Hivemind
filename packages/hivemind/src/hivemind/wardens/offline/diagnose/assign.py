"""Build the task a cut-off Warden gives its diagnostic Drone, and the grant it runs under.

The Drone's task never came from the Queen, so everything a `TaskAssign` and a `GrantIssued` carry
is made here, from the Warden's own facts (roadmap 11.9a, ADR-0056). The objective is the one the
roadmap states, "diagnose and, if possible, restore the link to the Queen at <host>", with the
host the Warden dials and, after a first Drone that found nothing, what it reported, so the next
one tries something else. The task asks for a closing text whose first line is `CAUSE: <the cause>`
(or `CAUSE: none found`), which is how the Warden reads what the Drone learned; the one acceptance
criterion is a judge's rubric that says so, because nothing here is machine-checkable and the
Warden, not the Drone, decides what the ending means. The objective also says what the Drone's
tools will not: a command shows it only whether it worked, never what it printed, and three failed
commands in one attempt stop it, so each check is run so as to write its output to a file, read
back, and always exit 0. The goal ceiling (`CEILING`) narrows the Drone to commands, reading files
and writing in scratch on its one slot: it can ask nobody anything, reach no network of its own,
keep nothing outside scratch and request no tool, because there is nobody to answer and the Cell's
access level and the Capping gate bound the rest as for any Drone. The grant is the Warden's own
local pool: one sub-bee, no seats, no spend, one local source, valid for the budget's wall-clock
and never sent anywhere.

Fits into the Hive:
    Layer 5 (per-Cell supervisors; spawn and supervise Workers), inside the wardens package's
    offline.diagnose sub-package. Called by `.drone` once per Drone. Calls into `hivemind.cell`,
    `hivemind.wardens.spawn` (LocalAssignment), `.decide` and waggle only.

Key invariants:
    - Pure but for the ids and the time it takes from the injected clock.
    - The task's origin is the Warden's: its id is minted here, under a grant minted here, and
      neither is known to the Queen.
    - The ceiling never widens what the Drone's role and the Warden's set already allow; it only
      takes away.
    - Nothing the objective names is a credential, a path or a model.

See Also:
    - docs/adr/0056-active-reconnection-diagnostic-drones.md for what the Drone is for.
    - hivemind.wardens.offline.diagnose.decide for `read_finding`, which reads its closing text.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from hivemind.cell import Cell
from hivemind.forage import ModelSlot
from hivemind.wardens.offline.diagnose.decide import Finding, LocalBinding
from hivemind.wardens.spawn.local import LocalAssignment
from waggle.clock import Clock
from waggle.ids import GrantId, TaskId, WardenId, new_grant_id, new_task_id
from waggle.messages import (
    AccuracyBar,
    HoneyClearance,
    Postcondition,
    PostconditionKind,
    Tempo,
)
from waggle.messages.forage import AllowedBinding, Effort, GrantIssued, SourceRef
from waggle.messages.task import TaskAssign, WorkerRole

# The goal ceiling every Drone of a search holds at most: commands, files, its one model slot and
# its tools. A Drone's role lists its tools as `tool:*` and a ceiling keeps only what one of its
# own entries covers, so naming the tools one by one would leave the Drone none of them. What it
# takes away is every family the other tools then ask for: `question:human` (`ask`), `net`
# (`http_request`), a write outside scratch (`keep`), Honey, Cell Wax and spend. `tool:request`
# is a family of its own that `tool:*` does not cover, so it cannot ask the Hive for a tool.
CEILING = (
    "exec:*",
    # Marks this a diagnostic task: below FULL on a Cell that does not contain its processes, its
    # Drone may run the read-only diagnostics alone (hivemind.guard.diagnostics, ADR-0060).
    "exec:@diagnostic",
    "fs:read:**",
    "fs:write:**",
    "llm:worker",
    "tool:*",
)
LOCAL_TOKEN_BUDGET = 200_000  # Tokens the local grant allows; a local model has no price to cap.
MAX_EARLIER_CHARS = 2_000  # How much of earlier Drones' reports the next one is shown.
# What the Drone's command tool does not say, said once: it reports only whether a command worked,
# and every command that exits non-zero counts toward the three that stop a Drone. So a check is
# run so that it writes what it printed into scratch and always exits 0.
COMMAND_HINT = (
    "A command shows you only whether it worked, never what it printed, and three failed "
    "commands stop you. So run each check as an argument list that writes its output to a file "
    "in your scratch directory and always succeeds, for example "
    '`["sh", "-c", "getent hosts <name> > out.txt 2>&1; echo exit=$? >> out.txt"]`, and read '
    "the file back. Below FULL access on a borrowed machine only a fixed list of read-only "
    'commands runs, each exactly as listed (for example `["ip", "route", "show"]`); there, '
    "read the files you need with read_file instead."
)
FINDING_RUBRIC = (
    "The closing message's first line is `CAUSE:` followed by the cause in one line, or by "
    "`none found`, and the rest says what was checked and what was changed."
)

__all__ = [
    "CEILING",
    "COMMAND_HINT",
    "LOCAL_TOKEN_BUDGET",
    "LocalPlan",
    "diagnosis_objective",
    "local_assignment",
]


@dataclass(frozen=True, slots=True)
class LocalPlan:
    """What a Drone's task and grant are made from (codingrules 5.1's argument group).

    Attributes:
        warden_id: The Warden that holds the grant and sends the Drone.
        cell: The Cell the Drone works on: the Warden's own.
        binding: The local model the Drone runs on.
        peer: The Queen's host, as the Warden dials it.
        earlier: What the Drones sent before this one reported, oldest first.
        valid_for_s: How long the grant lasts: what is left of the budget's wall-clock.
        attempt: Which Drone of the search this is, from 1.
    """

    warden_id: WardenId
    cell: Cell
    binding: LocalBinding
    peer: str
    earlier: Sequence[Finding]
    valid_for_s: float
    attempt: int


def diagnosis_objective(peer: str, earlier: Sequence[Finding]) -> str:
    """Write the Drone's objective: look for why the link went, fix it if you can, report.

    Args:
        peer: The Queen's host, or empty when the Warden knows no name for it.
        earlier: What earlier Drones of this search reported; the new one is told to try something
            they did not.

    Returns:
        The objective text.
    """
    where = f" at {peer}" if peer else ""
    lines = [
        f"Diagnose and, if possible, restore the link to the Queen{where}.",
        "",
        "This machine's Warden lost its connection to the Queen and keeps retrying with backoff. "
        "Look for why, as a careful operator would: whether the name resolves, whether a route "
        "and the port answer, the network interface, the resolver, proxy and firewall settings, "
        "and anything on this machine that changed lately. Change only what a fix needs and "
        "nothing outside what you are allowed to; undo anything that did not help.",
        "",
        COMMAND_HINT,
        "",
        "End with a closing message whose first line is `CAUSE: <the cause in one line>`, or "
        "`CAUSE: none found`; then say what you checked, what you changed and what happened.",
    ]
    if earlier:
        reported = "\n\n".join(finding.text for finding in earlier)[:MAX_EARLIER_CHARS]
        lines += [
            "",
            "An earlier attempt reported the following. Try something it did not.",
            "",
            reported,
        ]
    return "\n".join(lines)


def local_assignment(plan: LocalPlan, clock: Clock) -> LocalAssignment:
    """Make the Drone's task, its grant and the binding it runs on.

    Args:
        plan: The facts they are made from.
        clock: Mints the task's and the grant's ids and stamps the grant's expiry.

    Returns:
        The assignment, ready for `hivemind.wardens.spawn.spawn_local`.
    """
    task_id, grant_id = new_task_id(clock), new_grant_id(clock)
    expires_at = clock.now() + timedelta(seconds=plan.valid_for_s)
    return LocalAssignment(
        _task_of(plan, task_id, grant_id),
        _grant_of(plan, task_id, grant_id, expires_at),
        plan.binding.key,
    )


def _task_of(plan: LocalPlan, task_id: TaskId, grant_id: GrantId) -> TaskAssign:
    """The Drone's task: a low-tempo Drone on the worker slot with a rubric for its closing text."""
    return TaskAssign(
        task_id=task_id,
        goal_id=task_id,
        cell_id=plan.cell.id,
        role=WorkerRole.DRONE,
        slot=ModelSlot.WORKER.to_wire(),
        objective=diagnosis_objective(plan.peer, plan.earlier),
        acceptance=(
            Postcondition(
                kind=PostconditionKind.JUDGE_RUBRIC,
                subject="finding",
                argv=(),
                expected=FINDING_RUBRIC,
            ),
        ),
        capabilities=CEILING,
        tempo=Tempo(latency_budget_s=None, accuracy=AccuracyBar.LOW),
        clearance=HoneyClearance.C1,
        grant_id=grant_id,
        attempt=plan.attempt,
        resume_from=None,
        reason="The Warden is cut off from the Queen and sent this Drone from its own local pool.",
    )


def _grant_of(
    plan: LocalPlan, task_id: TaskId, grant_id: GrantId, expires_at: datetime
) -> GrantIssued:
    """The Drone's grant: one sub-bee on the one local source, free, until the budget runs out."""
    source = plan.binding.source
    reference = SourceRef(
        source_id=source.source_id,
        provider=source.spec.provider,
        model=source.spec.model,
        host_cell_id=plan.cell.id,  # Hosted on this very Cell.
    )
    return GrantIssued(
        grant_id=grant_id,
        holder=plan.warden_id,
        cell_id=plan.cell.id,
        task_id=task_id,
        revision=0,
        allowed=(
            AllowedBinding(
                slot=ModelSlot.WORKER.to_wire(), source=reference, max_effort=Effort.MEDIUM
            ),
        ),
        seats=(),
        token_budget=LOCAL_TOKEN_BUDGET,
        spend_budget=0.0,
        tokens_spent=0,
        spent=0.0,
        max_sub_bees=1,
        expires_at=expires_at,
        reason="The Warden's own local pool: nothing was asked of the Queen.",
    )
