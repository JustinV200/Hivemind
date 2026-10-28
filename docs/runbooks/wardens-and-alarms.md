# Wardens and Alarms

Every Cell has one Warden, the supervisor that spawns its Workers, watches them and gives the Cell
back when the work is done. When a Worker hits something it cannot fix, the Warden raises an Alarm,
and the Alarm climbs one level at a time: Worker, Warden, Queen, then you. This page is about what
you see at the top of that chain and what you can change about how it climbs. The design is ADR-0012
and ADR-0021.

## See the Wardens

```
hive wardens list --manifest hive.toml
```

It lists every Warden seen on the trail, with its latest state, the grants issued to it and its
sub-bees. A Warden is `ACTIVE` while it has bees working, `CLUSTERED` while its bees are paused,
`OFFLINE` while it cannot reach the Queen, and in `WATCH` while its Cell is idle. In `WATCH` it looks
at the Cell read-only, and once an hour by default it reviews what it saw, which is the Patrol
(ADR-0054).

The Observation Hive shows the same thing live, Cell by Cell, from any enrolled device.

## How an Alarm climbs

An Alarm keeps one id the whole way up, so the trail shows every hop. At each level the supervisor
looks its kind up in the escalation policy and does what the matching row says:

| Action | What happens |
|---|---|
| `RETRY` | the same bee tries again |
| `RESPAWN` | a fresh bee starts from the last Handoff |
| `REBIND` | the bee moves to the slot's fallback binding, usually a stronger model |
| `TAKEOVER` | the supervisor resumes the task from the bee's Handoff, through a fresh bee |
| `CANCEL` | the task is stopped for good |
| `QUARANTINE` | the bee is stopped, its memory tainted, its task held |
| `ESCALATE` | the Alarm goes up one level |

The Queen never runs a task herself, so her takeover is always a fresh bee on a Warden. Her last
step is to escalate to you.

What the shipped policy does with each kind:

| Kind | Policy |
|---|---|
| `WORKER_FAILED` | retry, then rebind, then escalate on the third attempt |
| `WORKER_CRASHED` | respawn, then rebind, then escalate on the third attempt |
| `WORKER_STALLED` | respawn, escalate on the third attempt |
| `CONTEXT_OVERFLOW` | respawn from a Handoff |
| `POSTCONDITION_FAILED`, `ACCEPTANCE_FAILED` | retry once, then escalate |
| `PROVIDER_UNAVAILABLE` | rebind once, then escalate |
| `GRANT_EXCEEDED` | escalate, since only the Queen divides Forage |
| `QUOTA_EXCEEDED` | cancel, since the lease already killed the command |
| `SECURITY`, `PATROL_ANOMALY`, `NURSE_UNRESOLVED` | escalate straight to you, never retried |

Anything without a row of its own, `OTHER` included, escalates.

## Answer what reaches you

```
hive inbox --manifest hive.toml
```

It lists the questions bees are waiting on, the Alarms escalated to you and not yet resolved, and
the attempts no Nurse Bee could review. Answer a question with:

```
hive inbox answer <question id> "use the staging database" --manifest hive.toml
hive inbox answer <question id> "the second one" --option 2 --manifest hive.toml
```

The running Hive picks the answer up on its next poll and the waiting bee carries on. An Alarm waits
in the running Queen's memory, so it is acknowledged through her Entrance, from the Observation
Hive's inbox or from a device enrolled with `hive remote enrol`:

```
hive inbox --remote acknowledge <alarm id>
```

The Attendant orders the inbox. It is a fixed scorer, not a model, so the same items always come
out in the same order (ADR-0013).

## What each kind usually means

- `WORKER_FAILED` or `ACCEPTANCE_FAILED` that reaches you: the task as planned is not working. Read
  the task with `hive tasks show <task id>` and the trail with `hive trail tail`, then answer or
  submit a better goal.
- `PROVIDER_UNAVAILABLE`: the model provider is down and the fallback is too. The Hive clusters the
  bees on it rather than limping along (`clustering.md`).
- `GRANT_EXCEEDED`: the task wants more Forage than it was given. `hive forage status` shows the
  split.
- `QUOTA_EXCEEDED`: a lease's scratch outgrew `[hive_stand] scratch_quota_mb`. Raise it if the task
  really needs the room.
- `SECURITY`: a bee was quarantined or the Guard Bee saw something. Read `docs/guard/guard-bee.md`
  before you lift anything.
- `PATROL_ANOMALY`: an idle Cell's Patrol found something, like a disk nearly full or a service that
  keeps restarting. Nothing failed. It is a heads up about the machine.
- `NURSE_UNRESOLVED`: the Nurse Bee still asked for changes after the last fix-forward round. The
  task is closed as failed and the Cell keeps the last attempt's work. The note beside the Alarm has
  the Nurse's findings.
- `CELL_UNREACHABLE`: a Swarm device dropped off. See `nucs.md` for what the Hive does meanwhile.

## Change the policy

The shipped table lives in the package (`hivemind/supervision/defaults/default-policy.toml`) and is
described in `docs/supervision/README.md`. To use your own, copy it and point the manifest at the
copy:

```toml
[supervision]
policy_file = "supervision/my-policy.toml"
```

The path is resolved against the manifest's folder. Each `[[rules]]` row is a `kind`, a
`min_attempts` and an `action`. For an Alarm, the row for its kind with the highest `min_attempts`
it has reached wins, then a row with no kind, then `default`. Keep `SECURITY` on `ESCALATE` from
the first attempt. The Queen and every Warden load the file once at start, so restart the Hive after
a change.
