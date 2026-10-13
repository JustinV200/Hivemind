# ADR-0053: A Nuc keeps working while cut off, every remote Warden reconnects, and the Queen reconciles a returning Warden instead of restarting it

- Status: Accepted
- Date: 2026-10-13

## Context

A Warden (the always-on supervisor of one Cell, a unit of compute) that runs on a different machine
from the Queen (the central orchestrator, on the Hive Stand) will lose its link: a Virtual Cell's
in-Cell Warden when the network between the Cell and the Hive Stand drops, a colonized device's
Warden (ADR-0052) when the laptop it lives on changes networks. Codingrules 8.8 and 15 set the
principle: a disconnected Warden keeps working within what it owns, queues what must be reported,
never grows, and the Hive clusters it past a manifest limit (Clustering: pause and preserve while
the Hive cannot think; ADR-0024). Roadmap steps 11.8 and 11.9 turn that into behaviour, and four
questions in it are hard to reverse because the Queen's tables, the trail (the Pheromone Trail, the
Hive's append-only audit log) and every device's Warden depend on the answers. Which of a Warden's
messages queue, which wait and which are refused while it is cut off, and who decides. What a Warden
with a model of its own may do that one without may not, which is what makes a device worth
promoting to a Nuc (a colonized Real Cell that also runs its own model server). How the Queen treats
a Warden that comes back: ADR-0005 gave the outbox (a durable queue of what a node could not send)
and ADR-0007 gave the trail segment (the slice of the trail a node wrote while cut off), but nothing
says that a returning `warden_id` is the same Warden and not a second one claiming its Cell. And how
a trail segment written offline earns its place in the audit: ADR-0007's merge settles order and
duplicates, and says nothing about whether the node that sent the segment wrote it.

## Decision

**A Nuc is a colonized Cell that owns its models.** A Cell is colonized when its Warden and its
sub-bees (the Workers the Warden spawns) run on the device itself, which is Level 1 of the ladder of
ADR-0052. Promoting a Level 1 device to a Nuc is the Queen's decision, taken from the device's free
VRAM, the Hive Stand's seat pressure and the tasks' need for autonomy (`decide_nuc`, ADR-0052). She
writes a hosting plan (which model sources the Cell uses, local ones first), sets the model ceilings
(the limits on the Warden's own pool of capacity) once and sends `NucPromote`. The device's Warden
starts a model server through `wardens/local_pool/hosting/` within those ceilings, loads allowlisted
models and reports each as a source (ADR-0044); from then on its local pool includes seats, and
demotion is the reverse. Everything a Nuc needs to keep thinking is in its own local pool
(everything physically on the device), which is the reason it can be left alone when the link drops.

**Every remote Warden reconnects through one link.** A Warden that dials the Queen, whether a
Virtual Cell's or a colonized device's, holds its link through `wardens/offline/link.py`, a
`ReconnectingQueenLink`. On a lost link the Warden enters `OFFLINE` and the link re-dials with
capped backoff, 0.5 s doubling to 30 s (the schedule of ADR-0004). On success it re-announces itself
with `cell.ready`, `forage.capacity_report` and `cell.heartbeat`, replays its outbox, ships its
trail segment and records `warden.reconnected`. The re-dial and the announcement are injected, so
one class serves both kinds of remote Warden.

**The offline rules are pure data.** `wardens/offline/policy.py` decides, with no I/O, what each
kind of message does while the link is down. Queued in the outbox: `task.result`, the
`task.progress` checkpoints, `supervision.alarm_raised` (an Alarm: an issue a bee cannot resolve,
escalated up the chain) and `forage.request` (a request for more shared Forage, the Hive's capacity
of compute, model seats and spend). Held, not sent: a human-bound `supervision.question`, which is
re-sent after reconnection while the asking sub-bee stays blocked, because only the Queen decides
when a question goes to the human (codingrules 8.8). Refused locally: a `cell.request`, and a spawn
that would need shared Forage beyond the frozen grant, because an offline Warden cannot grow
(codingrules 15). Shared grants (the Queen's leases on shared capacity) are frozen from the Warden's
side, neither renewed nor revoked locally; what the Queen did with them meanwhile is settled when
she re-sends them. The local pool stays fully usable.

**What keeps running depends on what the Warden can think with.** A Nuc keeps its sub-bees running
on local bindings. A Level 1 Warden (one on a colonized device that has no model server) with no
local model clusters its sub-bees at once, checkpointing and pausing them and keeping them warm,
since it has nothing to judge with. Both run full Clustering past `[supervision] max_offline_s`. A
subtask whose tempo needs a shared source files a `ForageRequest`, which waits in the outbox; it
does not fail, and it is answered after reconnection.

**Reconnection is reconciled.** The Queen treats a known `warden_id` re-attaching as a reconnection
and not as a new Warden. She swaps the link's transport under the existing Warden, re-sends the
current grants, ceilings and hosting plan (today ceilings are sent once per attachment, a residual
gap `queen/forage/ceilings.py` notes), and merges the trail segment. A merged segment must carry
`TrailSegmentSync.segment_signature` (Waggle 1.13): a padded base64 Ed25519 signature, made with the
node's registered key, over the segment's identity and digest. A Swarm node's segment without a
signature that verifies is refused and recorded as `guard.segment_refused` with the reason
`unsigned` or `bad_signature`. ADR-0007 covers order and duplicates on merge; this adds
authenticity, so a hijacked connection or a faulty node cannot put events into the audit under
another node's name. Duplicates the outbox replay produces are dropped by the replay window
(ADR-0055), silently inside the reconnection grace that follows `warden.reconnected`.

**The Queen holds a lost device before she judges it.** On a lost link she marks the node
`UNREACHABLE` and holds its tasks for `[swarm] unreachable_grace_s`, neither failing them nor
re-placing them. In that window she asks for the Hive Stand-side diagnosis (ADR-0056). Then she
decides. A Nuc's tasks keep waiting, up to `max_offline_s`, because the device is still working and
its results will arrive in the outbox. The tasks of a Level 0 or Level 1 device are re-placed,
because a Level 0 device's hands are cut off from their Warden and a Level 1 device has already
clustered.

## Consequences

Positive: an outage costs time and not work. Results, progress checkpoints and Alarms the Warden
produced are in a durable outbox and arrive in order, and a high-bar subtask waits for a shared
source instead of failing. A Nuc finishes what it started and is not duplicated onto another Cell
while it does. A returning Warden keeps its grants, ceilings, hosting plan and history because it is
recognised, and the Queen has one record of it. The audit cannot be extended by a node that did not
write the events: a segment is accepted only with the node's own signature. The rules are one pure
table, so they can be tested row by row, and the same table serves every remote Warden.

Negative: while cut off a Nuc may do a good deal of work the Queen cannot see, for up to
`max_offline_s`, and its results are accepted after the fact; the access level and the ceilings
bound what that work can touch, and nothing else does. A Warden on a device holds state the Hive
Stand does not, its Handoffs (the documents bees write before their context is reset), memory and
trail segments, so a device wiped during an outage loses them. The signature binds a segment to a
node's key and not to the truth of its contents: a compromised device can still sign any segment for
itself, and what the rule prevents is a segment under another node's name. A Level 1 Warden with no
local model stops its sub-bees at once, so a short blip costs more than it would on a Nuc. The
held-question rule leaves a sub-bee blocked for the whole outage, as it would be waiting online.

## Alternatives considered

Re-placing a Nuc's tasks the moment its link drops: the Nuc is still working, so two Cells would act
on the same files and the work would be done twice, which defeats the point of a Nuc. Failing the
tasks of a lost device: it throws away work the outbox and Handoffs preserve, and turns every
network blip into a human interruption. Treating a re-attaching Warden as a new one: its grants,
ceilings and hosting plan are lost, two records claim one Cell, and its trail segment has no owner.
Merging segments on duplicate detection alone, which is all ADR-0007 asked: a forged segment would
enter the audit, so the node's signature is required. Letting an offline Warden renew its own grants
or spawn past them: an offline Warden cannot grow. Answering a human-bound question locally or
failing it: the Queen alone decides when a human is asked, and a failed question loses the sub-bee's
place. Clustering a Nuc at once like a Level 1 Warden: it wastes the capability that makes a Nuc,
and Clustering past `max_offline_s` is the backstop anyway.
