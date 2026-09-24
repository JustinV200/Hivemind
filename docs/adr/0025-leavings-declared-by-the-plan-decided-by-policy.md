# ADR-0025: Leavings are declared by the plan, decided by policy, asked of the human only sometimes, and listed in a ledger

- Status: Accepted
- Date: 2026-09-21

## Context

A Real Cell is borrowed and left exactly as found (codingrules 8.7). Phase 3 implements that
literally: a lease's scratch directory is removed wholesale on release, and every write outside
scratch is recorded with the bytes it replaced and put back. The first real runs showed the cost:
a goal that asks for a file cannot keep it, because the file dies with the lease. `RestoreRecord`
has carried a `persist` flag since 3.17 that the releaser honours, but nothing could set it, and
the operator does not want to make that call by hand for every file. The rule that a Cell is left
as found matters more, not less, once the Swarm (phase 11) borrows other people's devices, so
persistence has to be added beside it rather than by weakening it. This ADR records who decides
that a path stays, what happens when the answer is no, and what "left as found" means afterwards.

## Decision

**The plan declares; a bee can never widen.** `PlannedTask.leaves` is a bounded tuple of
`PlannedLeaving` (a path pattern and a one-line reason), empty by default, carried unchanged on
the Task, the Assignment (waggle 1.3) and the Drone's brief. The planner is told to declare only
what the goal itself asks to remain. A path no plan declared is never persisted, whatever a tool
result or a bee says: "not declared" is the policy's first rule, checked before every other input.
The declaration comes from the model that read the human's goal, not from the model that is
reading web pages and tool output, which is the one a prompt injection reaches. A Drone that
thinks something else should stay can only raise a Question. The pattern's own validator (absolute
or `~`-rooted, never a bare root, drive or home nor a wildcard straight under one, no `..`) lives
in waggle so it holds on a bare wire decode; the inside-scratch rule runs in the planner's
structured-output ladder, so a bad pattern is retried rather than silently dropped.

**A pure policy table decides, and `DENY` restores rather than rejects.**
`supervision/capping/leave` is a pure `decide(request, cell, declared, table)` returning `ALLOW`,
`ASK` or `DENY` from data in `leave-policy.toml`: the Cell's access level (`READ_ONLY` and
`SCRATCH` always `DENY`), its Comb Shield (`NIGHT_VEIL` always `DENY`), whether it is the Hive
Stand or a borrowed device, the path's class (keep root, home, system, startup, other), size, and
whether the file is executable. It reads levels and capabilities, never `cell.kind`. The gate
consults it when it applies an `outside_scratch_write`. `DENY` does not reject the write: the task
may legitimately need the file to exist while it works, so the write applies with `persist=False`
and is put back on release exactly as before this ADR. `ALLOW` persists with
`approved_by = POLICY`. The judge's rubric for the tier also asks whether leaving the path
matches the task's objective, so an over-declaring plan is caught by a different model than the
one that wrote it.

**`ASK` is a HUMAN rung built on the existing Question chain.** `HumanCheck` raises a closed
Question (keep, keep for this whole goal, discard) through the same Worker, Warden, Queen and
`hive inbox` path as any other Question; the task blocks until it is answered. Only an Answer
whose source is the human approves. A timeout, any other source or a missing option means discard,
never a failed task, and no tempo removes the rung. "Keep for this whole goal" is remembered by the
Queen per goal and Cell; she answers later leave Questions for that pair herself, marked as the
human's, and records `queen.leave_remembered` naming the question the memory derives from. Because
the Queen recognises a leave Question by its closed options, the `ask` tool refuses those options:
a model must not be able to word the one Question whose answer is remembered. Phase 10 moves the
same Question onto push and step-up without changing anything here.

**What stays is ledgered, and the ledger defines "left as found".** On release, restore records
are grouped by path. A path with a persisted record is read back and written once to the Leavings
ledger (`cell/leavings`: Cell, path, sha256, size, task, lease, who approved and why, and the
bytes the path held before any Leaving existed), atomically with a `cell.left` event; everything
else is restored. Recording over an active row replaces it but keeps the original prior bytes, so
the same goal run twice never fails a release and removal always returns to the true original. A
kept path that has vanished by release is restored to its prior when it had one. "Left as found"
now means: the host equals its snapshot plus exactly the ledger's active paths, and `hive cells
leavings remove` makes it equal the snapshot again. Scratch is still removed wholesale on every
release.

**Moving a file out of scratch is an ordinary proposal.** The `keep` tool proposes a `COPY`
action (waggle 1.4, capped by a per-tier `max_copy_bytes`, since a diff cannot carry a binary) in
the `outside_scratch_write` tier, so it meets the same checks, policy, HUMAN rung and ledger as
any other write. `[hive_stand] keep_root` is an optional directory outside `scratch_root` that
outlives leases, `ALLOW` by default and counted against the disk reserve. A command's effects
cannot carry a restore record, so v0 scans only the task's declared patterns before and after each
command and ledgers what appeared or changed there; anything else a command touches remains a
`cell.touched_outside_scratch` event.

**Leavings are not the Basket.** A Leaving is a file left in place on a Cell. Bytes the Hive keeps
in its own store are the Basket (roadmap 9.2a). They have different owners, lifetimes and removal
paths and stay separate.

## Consequences

Positive: the operator is asked only where the table says a human should be, once per goal and
Cell at most. Everything left on a machine can be listed and removed, with the original bytes
kept, so the left-as-found test stays an equality rather than becoming a judgement. The decision
is data: a borrowed device's stricter rows are a toml edit, and phase 11 enrolment can ship its
own table. A destroyed Virtual Cell's rows are simply marked removed by the Undertaker (5.8).

Negative: the ledger stores prior bytes, so replacing a large existing file costs that much
SQLite space until the Leaving is removed. `remove` works per Cell, not per path, in v0. The
command scan sees only declared patterns, so a command that installs into undeclared locations
leaves them unledgered (and reported as touched, as today). An operator who sets `keep_root` must
also set `access_level = "FULL"`; the loader refuses the manifest otherwise and says why. A Warden
widens its lease and a sub-bee's `fs:write` slice to the keep root and the task's declared roots
only under `FULL` and only within the Warden's own ceiling; the lease is shared, so that
reachability outlives the task on that Cell, while the capability does not. The Queen's
keep-for-goal memory is in-process and is lost on a restart, which costs one repeated Question.

## Alternatives considered

Letting the Drone decide what to keep (a `persist` argument on its write tools): the bee that
reads untrusted content would choose what survives on the operator's machine. Rejected.

Asking the human every time: safe, and exactly what the operator asked not to have. The policy
table exists so the common case (a declared file under the keep root or in the home directory of
the Hive Stand) needs nobody.

`DENY` rejecting the write: simpler to explain, but it fails tasks whose work needs the path to
exist for a while (a config file a command reads), and it would make an over-cautious table break
goals instead of merely cleaning up after them.

Stopping the scratch wipe, or a `keep_scratch`-style switch for production: it weakens the one
rule borrowed devices depend on, keeps working files nobody asked for, and leaves nothing to list
or undo. `keep_scratch` remains a development flag only.

Folding Leavings into the Basket: the Basket moves bytes into the Hive's store; a goal that says
"install X" or "set up a project in Y" needs the files where they are.
