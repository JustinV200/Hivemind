# ADR-0029: Overwintering keeps a bounded pool of paused Virtual Cells, scrubbed on the way in, and never a Night Veil Cell

- Status: Accepted
- Date: 2026-09-21

## Context

Provisioning a Virtual Cell costs a container or VM start, a Python start and a Waggle handshake
before the first task can be granted (ADR-0027). A run with `prefer = "virtual"` pays that once per
task. Phase 5's exit criteria require a second run to reuse Cells and be measurably faster, and
Clustering (ADR-0024) wants somewhere to put Cells during a long outage that is cheaper than
keeping them running. Against that, a reused Cell is shared state between tasks, which is exactly
what a Virtual Cell exists to avoid, and codingrules 8.7 says a Night Veil Cell is created just in
time and destroyed when its task completes.

## Decision

**`hive/overwinter/policy.py` is a pure function** from a released Cell, the pool's contents and
the `[hive.overwinter]` manifest section to `OVERWINTER` or `TEARDOWN`. `hive/overwinter/pool.py`
holds the dormant Cells and performs the backend calls.

**A Cell overwinters only when all of these hold:** it is Virtual; its tier is not `NIGHT_VEIL`;
its task did not ask for `disposability = single_use`; its last task ended without a Capping
rollback of the whole Cell and without an open `BLOCK` Cell Wax; its backend can pause; the pool
has room for its image (`max_per_image`) and in total (`max_cells`); and paused Cells still fit the
disk Forage the manifest allots to the pool. Otherwise it is torn down.

**Going dormant scrubs the Cell.** Every sub-bee is stopped, scratch is removed, the grant is
revoked and the Warden goes quiet before the backend pauses the Cell. What deliberately survives
is the image's installed state and the volume's resident Basket blobs (9.2a), which is the point
of reuse.

**Coming back is a placement outcome, not a side door.** `decide` returns `ReuseDormant` when a
dormant Cell has the image the spec asks for (ADR-0028); the lifecycle resumes it, waits for the
Warden's heartbeat, and issues a fresh grant. A Cell that fails to resume within the ready timeout
is destroyed and placement falls through to a fresh provision.

**Dormant Cells expire.** Each has a `dormant_until` from `max_dormant_s`; the Undertaker destroys
expired ones on its sweep. Dormant Cells carry the Hive label, so `hive cells abscond` destroys
them too.

## Consequences

Positive: repeated Virtual runs skip provisioning. Long Clustering pauses stop paying for running
Cells. The Night Veil rule is a line in a pure function with a test, and the state machine refuses
the `DORMANT` edge for that tier as a second guard.

Negative: a reused Cell carries whatever earlier tasks installed, so two runs of one goal are not
bit-identical environments; a task that needs a clean machine says `single_use`. Paused Cells hold
disk and, on Docker, memory pages until the host reclaims them, which is why the pool is bounded
and accounted as Forage. Scrubbing trusts the Warden inside the Cell, so anything stronger than
"scratch removed" needs the snapshot rollback of step 5.10.

## Alternatives considered

A pre-warmed pool provisioned ahead of demand: pays for Cells nobody may use and needs a demand
forecast; reuse after first use gets most of the gain with none of the guessing. Snapshot and
destroy instead of pause: a cleaner reset but slower than a pause and unavailable on backends that
cannot snapshot; it remains available to a later policy row. Overwintering Night Veil Cells with
a scrub: any reuse is retained state across anonymised tasks, which the tier forbids outright.
