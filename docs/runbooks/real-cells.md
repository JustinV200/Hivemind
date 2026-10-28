# Real Cells: borrowing the Hive Stand and your own machines

A Real Cell is a machine the Hive borrows and gives back the way it found it. The first one is the
Hive Stand, the machine the Queen runs on. Every device you enrol in the Swarm is another
(`enrolling-a-device.md`). This page is about running work on them: what the Hive may touch, what a
lease leaves behind, and how to clean up when something goes wrong. The design is ADR-0010, and the
rules are in `docs/security.md`.

## Turn the Hive Stand on

The starter manifest `hive init` writes already has it on:

```toml
[hive_stand]
enabled = true
scratch_root = ".hive/scratch"
```

`scratch_root` is where each lease gets its own directory, resolved against the manifest's folder.
`docs/manifests/full.toml` has the rest of the section. The ones worth knowing:

- `access_level`, `SCRATCH` by default. It is the most any lease here may hold.
- `scratch_quota_mb`, 4096 by default. A lease's scratch that outgrows it has its command killed and
  a `QUOTA_EXCEEDED` Alarm raised.
- `disk_reserve_mb`, the free disk a new lease will not dip below.
- `keep_scratch`, for development only. It leaves each scratch directory behind so you can read a
  run's files afterwards, and the release then reports the Cell as not restored.

See what the Hive thinks the machine is with:

```
hive cells list --manifest hive.toml
```

It prints the Cell's id, kind, access level, Comb Shield tier, capabilities and capacity. The Hive
Stand's Cell id comes from `[hive] node_id`, so it stays the same across runs.

## Pick an access level

| Level | What a bee may do here |
|---|---|
| `READ_ONLY` | look only, nothing is written anywhere |
| `SCRATCH` | write inside the lease's own scratch directory, and run no commands (ADR-0060) |
| `FULL` | the whole machine, within the Cell's other controls |

The level caps every capability set issued for the Cell, whatever the policy says. At `SCRATCH` a
task that needs a shell is sent to a Virtual Cell if the Hive has one. With no `[virtual_cells]`
backend and no other Cell that may run commands, the task is cancelled with that reason, so a Hive
Stand that should run commands for itself needs `FULL`. Think about that one before you set it, since `FULL` on the Hive Stand is your own machine.

A task with `isolation = "required"` never lands on a Real Cell, and neither does a Night Veil task.

## What a lease does

Each task on a Real Cell runs under a lease, and the lease keeps track of everything it changes:

1. It opens with a fresh scratch directory and records `cell.leased`.
2. A write outside scratch needs a capability, goes through the Capping gate, and is recorded as
   `cell.touched_outside_scratch` with its path. The lease keeps what it has to put back.
3. On release it kills every process it started, restores what it recorded, removes the scratch
   directory and records `cell.released`.

A release that fails part way leaves the lease `ORPHANED` rather than stuck. The Undertaker retries
it, and so does the next start of the Hive, since every start is a Requeening (`requeening.md`).

## Leavings: what a task was allowed to keep

Sometimes a task is meant to leave a file behind, like a report it wrote into your home folder. The
plan declares it, policy decides it, and the lease records it as a Leaving instead of undoing it
(ADR-0025). List them with:

```
hive cells leavings list --manifest hive.toml
hive cells leavings list <cell id> --include-removed --manifest hive.toml
```

Take one back, or every Leaving on a Cell:

```
hive cells leavings remove --path <path> --manifest hive.toml
hive cells leavings remove <cell id> --manifest hive.toml
```

`remove` puts back what was there before (or deletes the file if nothing was) and records
`cell.leaving_removed` for each row. It refuses to run with neither a Cell nor a path, so it can
never clear everything everywhere by accident.

## When something is stuck

- A lease the Hive is not releasing: `hive cells release <lease id> --manifest hive.toml` asks the
  running Queen to release it on her next tick. If no Queen is running it says so and points at
  `hive abscond`.
- A Cell that is doing something it should not: `hive cells isolate <cell id> --reason "..."` cuts it
  off through the running `hive serve`, and `hive cells lift <cell id>` gives it back
  (`docs/guard/isolation.md`). The Queen never lifts an isolation on her own.
- An emergency: `hive cells sting-cut <cell or lease id> --reason "..."` disconnects at once. It asks
  for the operator password and a typed phrase (`docs/guard/sting-cut.md`).
- Everything: `hive abscond` tears the whole Hive down and releases every Real Cell lease
  (`absconding.md`).

`hive cells inspect <cell id>` shows a Real Cell's row and every lease the trail still shows open
on it, which is usually where to start.

## Desktop work on a Real Cell

A task that clicks, types or reads a screen gets a private display that its Warden starts in
scratch and stops when the task ends. The Cell's own screen is never used unless the operator opts in
with `real_display_allowed` and the task holds the matching capability. A browser-only task on the
Windows Hive Stand uses the browser fast path and needs no display (`phase6-windows-exit-check.md`).
