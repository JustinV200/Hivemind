# ADR-0060: Commands run only where they are contained or fully granted

- Status: Accepted
- Date: 2026-10-02

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

## Consequences

Positive: on a Hive Stand at SCRATCH no Worker command reads the operator's files, whatever its
cwd; the refusal is on the trail and readable by the Worker. ADR-0034's gap is closed for the
browser and the shell alike. A future sandboxed Real Cell only has to report
`processes_contained` from the code that sets the sandbox up.

Negative: a Hive with only a SCRATCH Hive Stand and no Virtual backend runs no commands at all;
an operator who wants that has to grant the Hive Stand FULL, which is honest about what a command
there can do. `TaskNeeds.commands` defaults to false, so a task whose plan leaves it unset is
still placed on a SCRATCH Real Cell, and its Worker learns from the refusal; the planner is told
which Cells run commands, but a model can still miss it. A Real Cell that becomes FULL later does
not revive a task already cancelled for this reason.

Known limits: acceptance criteria that run a command (`COMMAND_EXITS_ZERO`, `TEST_PASSES`) are run
by the Capping gate, not by a Worker tool, and are not covered by this rule; the planner writes
them and the operator's goal bounds them. Closing that needs the gate to know its Cell's ceiling.

## Alternatives considered

A per-OS sandbox rooted at scratch: the right end state, but a project of its own, with no portable
form for a borrowed Windows or macOS device. Running the ALLOWLIST rung for scratch commands: the
SCRATCH ceiling granted `exec:*`, so it would pass every command. A `cell.kind` branch in the tool:
codingrules 8.7 forbids it, and it would wrongly refuse a future sandboxed Real Cell. Defaulting
`TaskNeeds.commands` to true: every task would skip a SCRATCH Hive Stand, and a Hive without a
Virtual backend would place nothing at all.
