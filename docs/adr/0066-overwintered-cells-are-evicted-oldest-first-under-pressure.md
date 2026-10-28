# ADR-0066: Overwintered Cells are weighed at the disk they hold and evicted oldest first under pressure

- Status: Proposed
- Date: 2026-10-27

## Context

Roadmap 13.5, Overwintering at scale. The pool (ADR-0029) kept a bounded set of paused Virtual
Cells, but three things did not hold up once many Cells come and go:

- A dormant Cell was accounted at its spec's disk allotment, never at what it holds. A Cell given
  10 GB that wrote 300 MB counted 10 GB against `disk_budget_mb`, so the pool refused Cells it had
  room for.
- A full pool refused every Cell released after it filled. It kept whatever went dormant first,
  usually the least wanted, until each Cell's own `dormant_until`.
- `dormant_until` was enforced only by the Undertaker's startup sweep. A Hive running for days held
  expired Cells until it stopped.

QEMU snapshots and a cloud backend's stop are the other half of 13.5 and need those backends on
real infrastructure; this ADR covers the half every backend shares.

## Decision

**Disk is measured where a backend can.** `DiskMeter` is an optional backend capability, like
`EgressCutter`: a backend that declares `can_measure_disk` reports the bytes one Cell holds. The
lifecycle measures a Cell when it is released, weighs it at that figure against the disk budget,
and the pool accounts it at that figure while it sleeps. A backend that cannot measure is weighed
at its spec, as before. The fake backend measures (and a test can set what it reports); Docker and
QEMU do not declare it yet.

**A full pool makes room, oldest first.** On release the pool names the oldest dormant Cells whose
eviction would let the released one fit every bound (`hive/overwinter/room.py`, pure): only Cells
of the same image while the per-image bound is what blocks, any Cell otherwise, and nothing at all
if no eviction could make it fit. `decide_release` then decides against the pool as it would be
without them. Only if it decides `OVERWINTER` are they torn down, each with `cell.evicted`
(`reason = "pool full"`). A release that is torn down for any other reason evicts nothing.

**Expired Cells go while the Hive runs.** The release path evicts expired Cells before it weighs
room, and the dormant list the Queen reads on every placement pass and liveness tick evicts them
first too, so placement is never offered an expired Cell and an idle Hive does not keep one.

## Consequences

- The disk budget means what the pool holds, on backends that can say.
- The pool turns over towards recently used images instead of freezing on the first ones.
- A dormant Cell can be torn down by another Cell's release. Placement may have picked it a moment
  before; its resume then fails as a provision failure and the task waits for a fresh Cell, the
  path a resume that never becomes reachable already takes.
- Tearing an expired Cell down happens inside the Queen's tick. It is fast on the fake and on
  Docker; a slow backend would slow that tick.

## Alternatives considered

- **Evict on a timer of its own.** Another loop to start and stop, for work the Queen's existing
  reads already give a place to.
- **Least recently resumed instead of oldest dormant.** The pool keeps no resume history, and a
  Cell's dormant time already says which one has waited longest unused.
- **Measure disk at admission instead of release.** The decision to keep a Cell is made at
  release, so that is when the figure has to be known.
