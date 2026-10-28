# ADR-0065: Supersedure moves the Hive Stand with the old Queen frozen until the new one is acknowledged

- Status: Proposed
- Date: 2026-10-27

## Context

Roadmap 13.2a and codingrules 8.16: the Hive Stand is a role, not a machine. Supersedure moves the
Queen and her stores to another colonized Real Cell without losing a task, and the old machine
stays in the Swarm as an ordinary Cell. The pieces it builds on exist. Clustering (ADR-0024) pauses
every bee from its Handoff. `hive backup` and `hive restore` (ADR-0064) carry every store, the
Entrance tables and enrolled devices included, as one checksummed archive with no secret in it.
Requeening (ADR-0062) brings a Queen up from copied stores, reattaches the Wardens that redial and
places orphans afresh. The Pollen Packet already honours a `control.queen_moved` signed by the Hive
key (phase 11), but the notice carries no TLS pin, so a device could only follow a move whose new
listener presents the very certificate it pinned at enrolment.

What has to hold above all: never two Queens running. Two Queens over two copies of one Hive would
place the same task twice and grant the same Forage twice.

## Decision

**The candidate test (`queen/supersedure/preflight.py`).** A pure check over `CandidateFacts`:
the node is enrolled, colonized (Level 1 or a Nuc) and approved, with `FULL` access; it runs the
same runtime version; its free disk covers the stores plus a quarter again; its Forage covers the
Royal Reserve; every Swarm node and every Virtual Cell backend can reach it; its secret store holds
every secret the Hive holds, `hive.ed25519` as the same key; its clock is within 5 s of the Hive
Stand's. Every check is reported, passed or not, so `hive doctor --supersedure <node>` lists all
of them at once. Supersedure refuses to start on any failure.

**Human-only, on loopback.** `hive supersede <node>` is the operator at the Hive Stand: the console
logs in with the operator password, steps up, and types `supersede my hive stand`. The route
(`POST /v1/hive/supersede`) is served on the loopback listener only. The Guard's `supersedure`
point joins Absconding under the human-only floor, so no Queen, Warden, Worker, autopilot rule,
awake decision or tool can pass it whatever a role override grants.

**Freeze, then copy (`copy.py`).** The Queen clusters every configured provider (cause
`supersedure`), so every bee checkpoints and pauses and every lease and grant stays as it is. She
then moves `CLUSTERED -> SUPERSEDING` and records `queen.supersedure_started` (the candidate's
node, the sequence, the grace deadline) before the first byte is copied. While she is
`SUPERSEDING` her cluster tick neither probes nor resumes a provider, and she places nothing. The
stores move as one `hive backup` archive over the candidate's `CellSession` and are put back with
`hive restore --sha256` on the far side, which checks the archive's digest and every member again.
The Hive keypair never travels: preflight requires it already in the candidate's secret store.

**Handover order (`handover.py`).** The candidate's Queen starts from the copy in `REQUEENING`.
Her own trail holds the copied `queen.supersedure_started` naming her node, so she arms a
supersedure hold: she runs Requeening's first half (orphans taken back to PENDING) but places
nothing and does not settle, and records `queen.supersedure_ready` once. The old Queen polls for
that marker over the session until the grace deadline. On it she moves `SUPERSEDING ->
SUPERSEDED`, records `queen.superseded` in her own trail, acknowledges the candidate (which records
its own `queen.superseded`, lifting the hold), and only then sends `QueenMoved` (the next sequence,
effective now, both addresses honoured for the grace window) to every device link. Then she stops.
Acknowledging before announcing keeps the session to the candidate usable until the end, and means
no device is ever pointed at a Queen that has not been acknowledged.

**Rollback (`rollback.py`).** If the candidate is not ready by the grace deadline, or the copy
fails, the old Queen moves `SUPERSEDING -> CLUSTERED`, resumes every provider she clustered, tells
the candidate to discard (best effort; it records `queen.supersedure_aborted`), sends a
`QueenMoved` pointing back at herself with the next sequence, and records
`queen.supersedure_aborted`. Since every notice's sequence is strictly higher, a device that never
saw the move just keeps its address, and one that did is pointed back.

**Never two Queens, as data in each trail (`record.py`, `gate.py`).** On every start the
composition root reads the newest `queen.supersedure_started` and its outcome:

- It names this node as the old Queen and has no outcome: the Queen does not start. She cannot
  know whether the candidate took over after the grace window, so the operator settles it
  (`hive supersede --settle superseded|aborted`) after checking the candidate. Nothing rolls back
  on its own after a crash.
- It names this node as the old Queen and ends in `queen.superseded`: the Queen never starts here
  again. This machine is a Swarm node now.
- It names this node as the candidate and has no outcome: the hold is armed. It lifts only on the
  acknowledgement, or once the grace deadline has passed with the old Queen unreachable (then the
  candidate records `queen.superseded` with `acknowledged = false`).
- It names this node as the candidate and ends in `queen.supersedure_aborted`: the Queen does not
  start.

**QueenMoved carries the TLS pin.** `QueenMoved.new_tls_pin_sha256` is the new listener
certificate's SHA-256, required exactly when `new_address` is `wss://`. The packet pins it for the
new address, keeps the old pin for the old one until `grace_until`, and dials each address with its
own pin. It is additive: Waggle 1.16.

**What the old machine becomes.** Its Warden stays. It is not a Queen any more, and the operator
enrols it into the new Hive as a Swarm node in `WATCH` (`hive swarm invite` on the new Hive Stand).

## Consequences

- The old Queen is never active once the copy starts: `SUPERSEDING` and `SUPERSEDED` place nothing
  and resume nothing. The new one places nothing until she is acknowledged.
- The grace window bounds how long the Hive is frozen. A failed candidate costs one window of
  paused work, never a lost task, since every bee resumes from its Handoff.
- A device that is offline for the whole grace window never sees the notice and still dials the
  old address. It needs a new invite. The runbook says so.
- The expiry path (grace over, old Queen unreachable) is for a dead old machine. If the old machine
  is only partitioned and has rolled back, two Queens could come up on either side. The rollback
  notice's higher sequence keeps every device with the old Queen. The runbook tells the operator
  never to restart an aborted candidate without settling it.
- After an acknowledged move, the old machine's trail and the new one's each hold exactly one
  `queen.superseded` for that Supersedure.
- Phones and other Entrance clients reach the Hive through the VPN overlay. The move keeps their
  device records (they travel with the stores), but each phone's saved Entrance address is the
  operator's to update. Pushing the new address to them is left open.

## Alternatives considered

- **Announce first, then wait for readiness (the roadmap's order).** Devices would move to a Queen
  that may never be acknowledged, and the old Queen would lose the candidate's session the moment
  its packet followed the notice. Acknowledging first keeps one channel and one decision.
- **Let the old Queen roll back on her own after a restart.** She cannot tell whether the candidate
  took over after the window. A wrong guess is two Queens, so the human settles it.
- **Copy the stores over Waggle.** Waggle never carries a URL or a secret by design, and the
  session already moves files with Capping's checks. The archive's digest is checked twice.

## See also

- ADR-0024 (Clustering), ADR-0062 (Requeening), ADR-0064 (backups).
- `docs/runbooks/supersedure.md`, `docs/waggle/enrolment.md` section 12.
