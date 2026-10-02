# ADR-0060: Commands run only where they are contained or fully granted

- Status: Accepted
- Date: 2026-10-02 (amended the same day: the gate's own commands, and a plan that forgets the need)

## Context

`run_command` was confined only by its working directory. `workers.tools.session` tiered a
command by its cwd, so one run in scratch was `scratch_write`: the Capping gate ran SCHEMA and
SIZE_CAP, never the ALLOWLIST rung, and with it never the `exec:` capability check. On a Real Cell
the process (`cell.local.process`) is a plain subprocess with a file-size limit: it can read, copy
or change anything the Cell's user can, and on the Hive Stand that is the operator's own account.
`cp ~/.ssh/id_rsa .` followed by `read_file` walked straight past ADR-0034, which recorded the gap
in its consequences. Running the ALLOWLIST rung alone would not have closed it either, because the
SCRATCH access level granted `exec:*` (phase 6 handoff, section 4.2, item 1).

A filesystem sandbox rooted at scratch (bubblewrap, Landlock, a Windows job object with a
restricted token) would let a SCRATCH Real Cell run commands safely, but it differs per OS, needs
privileges or kernel features a borrowed device may not have, and is work of its own. The operator
decided the narrower rule below until one exists.

## Decision

**Below FULL access, a command runs only on a Cell that contains its processes.** Containment is
a capability, not a kind: `CellCapabilities.processes_contained` is true where every process the
Cell runs reaches no further than the lease. Whoever makes the Cell says so: the Docker, QEMU and
fake backends, the Queen's listener for a Cell she provisioned, and the in-Cell Warden for its own
container. It never travels on the wire, so no device can claim it for itself, and a Real Cell
leaves it false.

**The access level decides, in one table.** `guard.access` moves `exec:*` from SCRATCH's grants to
FULL's, and `ceiling_for(..., processes_contained=True)` adds it back at SCRATCH. `admits`,
`cap_to_access` and `warden_set` take the same flag, and `PolicyContext.processes_contained`
carries it to the access-level rule. So a Warden on the Hive Stand at SCRATCH holds no `exec`, and
neither does any Worker it spawns. A Virtual Cell is always FULL, so it runs commands either way.

**`run_command` asks the whole policy before it proposes.** It checks `exec:<argv[0]>` at the
`tool_invocation` point through the Worker's Enforcer (it used to ask only the floors). On an
uncontained Cell below FULL the access-level rule refuses it as `guard.access_level.scratch`, a
`guard.denied` row on the trail, and the Worker reads "this Cell cannot run commands at access
level SCRATCH, because its processes are not contained; it needs a Virtual Cell, or FULL access".
No proposal is made and no process starts.

**Placement keeps such work off such Cells.** `TaskNeeds.commands` (default false) says a task's
Worker runs commands. Placement rule 4d (`queen.placement.rules.command_shortfall`) asks the
ceiling the Cell's own Warden will build, the way rule 4c does for the Exoskeleton: a Real Cell
whose ceiling holds no `exec` is skipped, named in the reason, and the task goes to a Virtual Cell.
When no attached Cell may run commands and the Hive has no Virtual backend or dormant Cell at all,
the `PlacementError` is final and the dispatcher cancels the task with that reason rather than
leaving it to wait. The planner's fleet summary marks each Cell "commands" or "no commands" so the
model knows to set the need.

**Commands the gate runs itself meet the same rule.** A `COMMAND_EXITS_ZERO` or `TEST_PASSES`
postcondition (checked by the Capping gate after applying a proposal) or acceptance criterion
(checked by the Warden on its own session) runs a command a model wrote. `check_postcondition`
now takes a `supervision.capping.CommandAdmission` built from the `Cell` the gate already holds
(its access level and `processes_contained`, never its kind) and the Warden's `Enforcer` and
principal (`CommandGuard`, `GateDeps.command_guard`). On a Cell that may not run commands the
command never starts: `Enforcer.check_access_level` (the access-level ceiling alone, since no held
set governs the gate's own check) records `guard.denied` under `guard.access_level.<level>` as the
Warden, and the criterion fails with that reason and `commands_refused`, so the proposal rolls
back or the task's acceptance fails, never passes silently.

**A plan that forgets the need is corrected at both ends.** The planner sets `needs.commands`
itself for every task whose acceptance runs a command, since its Warden will run that command on
the task's Cell. A task that still lands on a Cell that refuses its commands (its Worker ran one
the plan never mentioned) is re-placed rather than retried there: the Worker's claim carries
`ResultCause.CELL_RUNS_NO_COMMANDS` (a new optional `TaskResult.cause`, Waggle 1.11) when a
`run_command` came back as an access-level refusal, and the Warden carries it onto a failed
result, adding it itself when one of its command criteria was refused. The Queen's table maps
such a FAILED result to `REQUEUE_TASK` while the task does not yet need commands; the Brood
Chamber's one new edge, `RUNNING -> PENDING` (`requeue_for_commands`), sets `needs.commands` and
nothing else in the spec, clears the placement and advances the attempt; the task's grants on the
refusing Cell go back to the pool, and a dispatch pass places it where rule 4d lets it land. When
placement says no Cell ever could (the final error above), the task fails with that reason
instead. A task already needing commands is never re-placed again, so this happens at most once
per task, and a claim that passes acceptance despite a refused command simply succeeds.

## Consequences

Positive: on a Hive Stand at SCRATCH no Worker command reads the operator's files, whatever its
cwd; the refusal is on the trail and readable by the Worker. ADR-0034's gap is closed for the
browser and the shell alike. A future sandboxed Real Cell only has to report
`processes_contained` from the code that sets the sandbox up.

Negative: a Hive with only a SCRATCH Hive Stand and no Virtual backend runs no commands at all;
an operator who wants that has to grant the Hive Stand FULL, which is honest about what a command
there can do. A task whose Worker needs commands its plan never named still spends one attempt
on a Cell that refuses them before it is re-placed. A Worker that tries one command it could have
done without, on a Hive with nowhere that runs commands, fails its task with the placement reason
if its acceptance then fails. A Real Cell that becomes FULL later does not revive a task already
cancelled or failed for this reason.

Known limits: the cause is read from the attempt that claims; a refusal in an attempt that then
handed off is seen again only if the resumed attempt tries a command too. `TaskResult.cause` is a
1.11 field, so a Worker or Warden older than that never sends it, and its tasks are retried as
before.

## Alternatives considered

A per-OS sandbox rooted at scratch: the right end state, but a project of its own, with no portable
form for a borrowed Windows or macOS device. Running the ALLOWLIST rung for scratch commands: the
SCRATCH ceiling granted `exec:*`, so it would pass every command. A `cell.kind` branch in the tool:
codingrules 8.7 forbids it, and it would wrongly refuse a future sandboxed Real Cell. Defaulting
`TaskNeeds.commands` to true: every task would skip a SCRATCH Hive Stand, and a Hive without a
Virtual backend would place nothing at all. Re-placing through `redispatch`: it resends to the
Cell the task is already on, which is the one refusing it. Failing the task and submitting a copy
needing commands: its dependents would be cancelled with it, and the goal would lose the task's
identity on the trail.
