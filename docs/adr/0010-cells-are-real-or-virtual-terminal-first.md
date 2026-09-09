# ADR-0010: Cells are Real or Virtual, terminal-first, behind one abstraction

- Status: Accepted
- Date: 2026-09-08

## Context

Every Worker (a subagent performing one role) runs in or on a Cell: an isolated Virtual Cell the
Hive provisions and later destroys, or a Real Cell, an existing device -- the Hive Stand (the
machine the Queen runs on) or a device enrolled in the Swarm -- borrowed for a task and left
exactly as it was found (README "Core concept 4", coding rules section 8.7). The README makes this
distinction central to how HiveMind is pitched: it can use the machine already in front of the
operator, not only spin up disposable infrastructure. But the distinction must be invisible above
`cell`: a Worker, a tool, and Placement's own decision logic should describe what a Cell can do
(has a display, can run a command, reaches which networks) and never what kind of Cell it is,
because a `CellKind` branch anywhere outside placement and the Undertaker breaks the moment a third
kind of Cell (say, a Nuc) needs different handling nobody wrote for. `scripts/
check_no_kind_branches.py` makes that a mechanical rule, not a review nudge. At the same time, a
Real Cell's lease behaves nothing like a Virtual Cell's provisioning: nothing is created or
destroyed, a scratch directory and a list of started processes exist for a bounded tenancy, and
`release()` must restore the device rather than tear anything down. Phase 3 needs the first working
vertical slice with zero external infrastructure, so the first Real Cell source has to be the Hive
Stand itself: no VM to boot, no device to enroll, nothing to provision -- the Warden's default home
already exists the moment the Queen's process starts.

## Decision

`cell/` defines one abstraction for both kinds: `Cell` (id, kind, name, source, `CellCapabilities`,
`ForageCapacity`, `AccessLevel`, `CombShieldLevel`) and `CellKind` (`REAL`, `VIRTUAL`), with a
validator that a `REAL` Cell is never `NIGHT_VEIL` and a `VIRTUAL` Cell is always `FULL` access.
`CellCapabilities` merges platform facts (os, arch, distribution, shell, package manager, Python)
with capability flags (display, audio, browser, can-start-display, can-host-model, network scopes)
on one model, converting to and from the two separate wire reports (`PlatformReport`,
`CellCapabilitiesReport`) that describe a Cell over Waggle, the same split `hivemind.forage.
HostCapacity` uses for the identical reason. A session is a terminal: `CellSession` offers `exec`
(an async generator streaming `OutputChunk`s and ending in exactly one `ExitStatus`, or raising
`CommandTimeoutError`/`SessionClosedError` instead), `put_file`, `get_file`, a `scratch_dir` every
relative path resolves against, and an idempotent `close`. `run(session, spec)` is a convenience
that drives `exec` to completion for a caller that only wants the final result.

A Real Cell's tenancy is a `RealCellLease` moving through its own state machine
(`REQUESTED -> OPEN -> RELEASING -> RELEASED`; `OPEN -> ORPHANED -> RELEASING` for a sweep,
`lease_state.py`, one transition table per coding rules section 9). It owns its mutable bookkeeping
in place -- `state`, started process ids, touched paths -- documented per coding rules section 8.5,
and its frozen facts (id, cell_id, holder, task_id, scratch_root, access_level, comb_shield,
allowed_paths) are computed once by whichever `RealCellSource` opens it. `open()` and `release()`
each write their own trail event (`cell.leased`, `cell.released`) in the same call that changes
`state`; `release()` is idempotent and delegates the actual killing and path restoration to an
injected `LeaseReleaser` Protocol, so the identical bookkeeping and trail-writing code serves both
`FakeCellSource` (in-memory, for tests and demos) and the Hive Stand's own source without either
reimplementing it. `RealCellLease` deliberately holds no `CapabilitySet`: coding rules section 4
puts `guard` above `cell` in the layer table ("nothing at Layer 2 or below imports guard for an
enum"), so a Warden builds a `CapabilitySet` from a lease's `access_level` and `scratch_root` via
`guard.access.ceiling_for` once it holds both, rather than `cell` importing `guard` to compute one
itself. `RealCellSource` (`name`, `cells()`, `lease(request)`, `open_session(lease)`) is the one
shape every Real Cell producer implements; `CellIdentity` (hive, node, actor) is the small bundle a
source stamps on its trail events, mirroring `brood_chamber.chamber.base.ChamberIdentity`.
`Snapshotter` is a Layer-2 protocol (`snapshot`, `rollback`); `NoopSnapshotter` is what every Real
Cell source hands out, since rolling back somebody else's borrowed machine is not a promise a lease
can make -- `snapshot` succeeds trivially and warns once per Cell id, `rollback` always raises
`SnapshotUnsupportedError`.

The Hive Stand (`cell/local/`, phase 3 step 3.11) is the first `RealCellSource`: standard library
only, because it is where the Queen's own process already runs, so there is nothing to provision
and nothing to enroll. `probe.py` reads the running machine's capabilities and capacity with POSIX
and Windows shims side by side, best-effort everywhere except a truly unusable host (`ProbeError`
on zero cores). `LocalProcessSession` implements `CellSession` over `asyncio.create_subprocess_exec`
(argument lists only, never `shell=True`), killing the process tree on a timeout and reporting every
started pid back to its lease. `HiveStandSource` hands out exactly one Cell, refuses a lease while
disabled or already leased, gives each lease its own scratch directory under the configured
`scratch_root`, and its `HiveStandLeaseReleaser` terminates every process the lease started and
removes the scratch directory, reporting what could not be restored. This is also the first
end-to-end proof of the whole vertical slice -- lease, session, release, trail -- with zero
external infrastructure to stand up first.

## Consequences

Positive: Placement, a Worker's tools, and every layer above `cell` write one code path for "run a
command" and "touch a file" regardless of where the Cell came from, and `check_no_kind_branches.py`
keeps that true by construction rather than by discipline. The identical `RealCellLease`/
`LeaseReleaser` split means a fake Real Cell source is exactly as capable as the Hive Stand from a
caller's point of view, which is what makes the two contract suites (`test_cell_session_contract`,
`test_real_cell_source_contract`) meaningful: a new source or session only has to pass them, never
reimplement lease bookkeeping. Starting the Real Cell story with the Hive Stand rather than the
Swarm or a Virtual Cell backend means phase 3 proves the Queen-to-Warden-to-Worker path with no
Docker, no cloud credentials, and no second machine.

Negative: `RealCellLease` holding no `CapabilitySet` pushes that computation up to every Warden
(and to `workers.capabilities`, a later phase), which must remember to call
`guard.access.ceiling_for(lease.access_level, lease.scratch_root)` itself; a Warden that forgets
gets no attenuation at all rather than a compile error, since nothing in `cell` can enforce it.
`NoopSnapshotter`'s once-per-Cell-id warning is process-local state with no persistence, so a
restarted Queen warns again for a Cell it already warned about before the restart -- acceptable
for a one-line operational nudge, not for anything an audit needs to rely on (the trail's own
`cell.*` events are the audit surface, not this log line). The Hive Stand's `HiveStandConfig`
capacity overrides (`max_sub_bees`, `cores`, `memory_bytes`) are defined in `cell/local/` rather
than read from the manifest directly this phase, so the `[hive_stand.capacity]` section a later
manifest dispatch adds must be mapped onto it rather than the reverse.

## Alternatives considered

A `Cell` class hierarchy per kind (`RealCell`, `VirtualCell` subclassing a common base): the
natural object-oriented instinct, but it invites exactly the `isinstance`/kind-branch pattern
coding rules section 8.7 forbids the moment a caller needs to special-case one subclass, and a
capability difference (has a display, or not) would end up expressed as a type check instead of a
data field a `Cell` can carry regardless of kind.

A Docker-first design for the very first Cell source, deferring the Hive Stand until Real Cells are
otherwise proven: rejected because phase 3's own goal is the first end-to-end slice with zero
infrastructure, and requiring a container runtime before the Queen can run a single command would
contradict that; Virtual Cell backends (`hive/backends/docker.py`) get their own ADR when phase 5
builds them.

A session that is not a terminal -- a structured RPC surface (`run_tool`, `list_dir`, `read_config`)
instead of `exec`/`put_file`/`get_file`: more discoverable for a narrow set of operations, but it
would need a new RPC method every time a Worker's tool needed one more thing from the Cell, where a
terminal already composes arbitrarily (coding rules section 1, "terminal first, peripherals on
demand"); the Exoskeleton (display, input, audio) is the deliberate exception, attached only when a
task actually needs it, never folded into the base session.
