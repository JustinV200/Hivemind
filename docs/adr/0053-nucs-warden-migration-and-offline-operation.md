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

## Amendment (2026-10-14): a colonized device's terms, and a Nuc across a Warden restart

Two gaps in the colonized rungs closed together, because the same restart answers both.

**A change of terms reaches a colonized Warden by restarting it.** A Level 0 device's Warden is on
the Hive Stand, so the gate rebuilds it when the operator runs `hive swarm access` or `shield`. A
colonized device's Warden holds the access level and tier it was started with in its environment, has
no message that changes them, and runs its commands through a lease of its own that the packet's
checks do not constrain; so the registry changed and nothing on the device did. The Queen now tells
the device in two parts and never waits for it to go idle. Her first pass that finds the terms
different sends the packet `swarm.node_update`, whatever its Warden is doing, and the packet's own
checks apply them from its next request. The Warden is then restarted by the packet she already has:
its tasks are checkpointed into Handoffs and the Cell is held, the packet stops the Warden (WITHDRAW)
and starts it again (START), which builds its environment from the terms the packet now holds, the
Queen waits for it under the same Warden id, checks that its `cell.ready` names the new terms, starts
a Nuc's model server again and resumes every task from its Handoff. The rung and the Warden's id do
not change, so a Nuc stays a Nuc. The terms a Warden runs under are what its `cell.ready` said, kept
by the gate; a Warden that has said nothing is left alone. If any step fails after the tasks were
paused, the device falls back to Level 0 (a Nuc through the colonized rung), where the Hive Stand's
Warden is built from the registry's row and so carries the new terms by construction, and the tasks
resume there; the operator can colonize it again once the cause is put right.

Withdrawing and colonizing again was rejected: it moves the Warden to the Hive Stand and back, a Nuc
would lose its server twice, and a withdrawal waits for an idle Warden, which would make a narrowing
wait. A Warden that takes the terms itself was rejected as the heaviest: a new message, and a lease
and a set of ceilings that today are built from the terms at start would have to change mid-life,
in `wardens` and in the packet. The restart needs neither.

**A Nuc whose Warden restarted is shown to run its server, or lowered.** A Nuc's model server is a
child of its Warden, so a Warden that restarted (it crashed, the device rebooted, the Queen restarted
it for new terms, or the Queen herself restarted) runs none while the registry still says Nuc. Every
`cell.ready` of a device recorded as a Nuc now makes it unproven until a flow of the Queen's has seen
the server answer. The restoration repeats the order that made it a Nuc (idempotent on the Warden's
side), naming the server and the models from the ceilings the Queen set for that Warden and not from
the device's free VRAM now, and writes the hosting plan again with the device's own models first. If
the Warden cannot start it, or the Queen holds nothing to restore from, she stops anything it
started, lowers the rung (`swarm.demoted` with the word that says why), takes the device's sources
off her map and plans from the shared pool, exactly as an operator's demotion would. A Warden that
cannot be reached decides nothing and is tried again when it can. A re-attach whose stored hosting
plan names a source that has since left the map (a Warden's server stops with it) no longer fails:
that plan is not sent, and the restoration writes the next.

## Amendment (2026-10-14): what the Nuc scenario found

Three things the real cable showed that the in-process rigs could not.

**A held task asks, and is answered when the link is back.** The Warden held a task whose bee needed
a hosted model, and said nothing of it. It now files a BINDING `ForageRequest` for it as it holds it
(`wardens/offline/wait.py`): the slot the task ran on and the grade its tempo asks of a model, and
nothing a grant could grow by, since an offline Warden cannot grow. The link queues it with the
results; the Queen reads it on the way back and, today, answers that a binding is routing's to pick
and not the ledger's, which is an answer. The Warden does not wait for it: it starts the task again
when the link is back. The reply is recorded, not judged: it used to reach an awake episode.

**The trail is sent again from before the loss was noticed.** A send that returns has put a frame
on a connection, not in the Queen's hands. A cable pulled out from under a device leaves the
connection looking alive until the websocket's keepalive gives up, forty seconds after the last
traffic, and every trail sync in that time returned as if it had arrived while the sync's cursor
moved on, so those events were never on the Hive's trail. On a reconnection the sync is rewound by
twice the keepalive (`UNNOTICED`) before it ships, and the Queen drops what she already holds by
event id. Frames that are not the trail (a result, an Alarm) sent in that stretch are still lost:
only what the Warden queued after it noticed is replayed, and nothing acknowledges the rest. A task
that finishes in the forty seconds after a cable is pulled therefore has its result lost, which is
the next thing to close (an acknowledgement of results, or a short memory of what was sent live that
a reconnection replays under the same ids, which the Queen's replay window already drops).

## Amendment (2026-10-14): what was sent while the loss went unnoticed is sent again

The gap the last amendment left is closed, with nothing new on the wire. A cable pulled out from under
a device leaves its connection looking alive until the keepalive gives up, and a `task.result`, an
Alarm, a `forage.request` or a deposit of Nectar (the Hive's shared knowledge) sent in that time
returned as sent and was lost, while the outbox, which acknowledges a frame once it has been sent,
had nothing to replay. The Waggle spec already asks every receiver to drop a duplicate envelope id
(ADR-0055), so the answer is to send the stretch again and let that window say what the Queen
already held.

**A link remembers what it sent, and sends it again first.** `waggle.transport.recent` holds
`RecentFrames`, a pure ring bounded by 256 frames, 4 MiB and the time (a frame is due only if it
was sent less than twice the keepalive, 80 s, before the connection ended), and a
`RememberingTransport` that remembers each frame it sent whole. A `ReconnectingQueenLink` wraps
every connection it dials in one, and on the next connection sends the due frames again, oldest
first and under the ids they had, after the announcement and before the outbox. Which frames is
the offline policy's table, read once more (`policy.must_arrive`: the rows that queue or hold), so
there is no second list to keep in step; the frames that the table drops (heartbeats, a progress
stage a later one replaces) or refuses are not sent again, which is narrower than "everything but
heartbeats" and was chosen so that the ring cannot grow a second policy. The Pollen Packet's
`Courier` does the same for the frames its outbox holds the kinds of, a `cell.lease_released` and a
terminal `session.exit` (gateway-contract section 14). A request keeps its own id when it is sent
again, unlike a request replayed from the outbox, which goes out in a fresh envelope: most of what
the ring holds was heard and answered, and a fresh id would have the Queen answer it twice.

**Two bounds keep a copy from being handled twice.** A peer that closed with a close frame read
everything before it, so the ring is emptied then; a Queen who shut down cleanly and came back has
no replay window to drop a copy, and none is sent. A frame is not sent again after 540 s (the
window of 600 s, less a minute): beyond that a frame she handled and forgot would be handled again,
so after a long outage the ring lets go, and a result sent in the unnoticed stretch of an outage
that long is lost as it was before. A frame sent again is remembered again, since the new
connection may be dead too.

**What the Queen needed.** Every listener a remote Warden or packet can reach is built with the
replay window, and a duplicate of each re-sent kind is dropped before a handler: a `task.result`
completes its task once (and a terminal task's result is recorded, not applied, if it ever got
past), an Alarm is raised once (the window alone guarantees it: the Alarms handler does not
deduplicate by `alarm_id`), a deposit is stored once (by the window, and again by the digest of its
content). Two things in the Queen were not safe. The reconnection grace that makes the dropped
duplicates silent began at `warden.reconnected`, which is recorded after the trail has been
shipped, and a sent-again frame is read right behind the handshake: a slow trail merge then turned
the whole stretch into `guard.replay_refused` records, the dire pattern. The link now notes the
attach before it reads (`LinkHooks.on_returning`, on both listeners). And the Queen's per-link
queue threw away what the old link had read and the tick had not yet taken when a Warden attached
again; the window had seen those frames, so their sent-again copies were dropped and the frames
were lost. The queue now moves them to the new link's.

**What is left.** A Queen who is restarted after a connection was lost without a close frame has
an empty window, and the Warden sends the stretch again: a result is recorded again (the task is
terminal) and a deposit is stored once by its digest, but an Alarm raised in that stretch for a
task that is not terminal, and a question, are handled twice. A restarting Queen is rarer than a
cable pull and the stretch is 80 s; an Alarm handler that deduplicates by `alarm_id` would close
this and is the next step if it shows. Frames the Queen's reader had taken from the socket when she
was shut down are lost with her, as they always were.

Alternatives: an acknowledgement of results (a new message and a protocol version, for a loss that
the receiver's existing window already makes harmless); a shorter keepalive (it narrows the stretch
and never closes it, and costs a ping every few seconds on a radio); keeping every frame in the
outbox until the Queen answers (the outbox acknowledges on send by design, ADR-0005, and most kinds
have no answer).

## Amendment (2026-10-14): a task taken back from a colonized device resumes from the Handoff she can hold

Taking back a lost Level 0 or Level 1 device's tasks "from their last Handoff" held for Level 0
only. A Level 0 device's Warden is on the Hive Stand, so its bees wrote their Handoffs in the
Queen's own store and she read the newest. A colonized device's bees wrote theirs in the device's
store, which a lost device takes out of reach, so she resumed those tasks from nothing.

**What reaches her.** Only the HANDOFF Nectar. A bee deposits every checkpoint's Handoff as Nectar
(ADR-0035), its Warden relays it and her intake keeps it; the deposit is best effort, and a device
lost before it was relayed leaves her the checkpoint before. The `task.progress` at CHECKPOINTED
names a Handoff and its Warden does not forward it; the trail segment a Warden syncs carries a
`memory.checkpoint` event's ids and counts and never its text (codingrules 12). No new message was
needed, but the Honey Store had no read by task, so `HoneyStore.handoff_nectars` is that read.

**What she does with it.** `queen/swarm_gate/recall.py`, for every task taken back: if a deposit is
newer than the Handoff she holds, she files it in her own memory as a checkpoint of her own and the
task's next placement resumes from that (inline when its Warden is on another node, as for any
Handoff she holds). She files nothing that a Night Veil Cell wrote (ADR-0030; the store does not
offer its rows, and a colonized device is never of that tier), nothing her trail says is tainted
and not since cleared by a judge, nothing written since the first suspect instant of an isolation
that stands (its Warden may never have been given the order to label it), and nothing above the
task's clearance. The Nectar's label counts when something raised it, and does not when only the
Real Cell floor holds it up: a device is a borrowed Cell, so everything it deposits is C2 in Honey,
which guards retrieval and not the task's own state, and the Handoff of a Level 0 device has always
been trusted at the label its bee gave it. A refusal is never an error: she resumes from the
Handoff she holds, or starts fresh, and `queen.decided` says why.

**What is left.** The same blindness is in the places that pause a colonized Warden's bees and
read the Queen's Bee Bread for the checkpoint: a renewal of its terms and Clustering's resume. They
resume from the Handoff she holds, which for a colonized device is an old one, and wait out their
patience for a checkpoint they cannot see; calling `recall_handoff` there is the next step. The
taint label is not carried by Nectar (`Nectar.tainted` is reserved and no Honey ledger exists), so
a taint her trail never heard of, from a device lost before its segment shipped, is not seen.

## Amendment (2026-10-14): a Warden's renewal and Clustering's resume recall the Handoff too

The last amendment left two places reading only the Queen's own Bee Bread for the Handoff a paused
task resumes from, and both now ask the recall (`recall_handoff`) as the take-back does.

**A move or a renewal notes the recalled Handoff.** `pause_for_move` (the pause under a
colonization and under the restart of a Warden for new terms) noted, for each task it paused, the
newest Handoff in her Bee Bread. For a colonized device's Warden that is the one the colonization
carried, because the bees have been writing theirs in the device's store since, so a renewal sent
the new Warden back to a checkpoint older than the bee's last, and the work between the two was
done again. It now notes what the recall returns: the HANDOFF Nectar the bee deposited as the pause
asked it to checkpoint, filed in her memory as a checkpoint of her own, or the Handoff she holds
when no deposit is newer or none may be used. The new Warden is sent that one inline, as for any
Handoff she holds (`TaskAssign.handoff`).

**Clustering's resume recalls per task.** `queen.cluster.protocol.resume` resumed each paused task
from the Handoff in her Bee Bread, which for a task on any Warden that is not hers (a colonized
device's, a Virtual Cell's) is an older one or none, and a task with none was resumed from nothing.
It now asks the recall on the task's own Cell. A Virtual Cell's bees deposit their Handoffs as Nectar
like any other, so the same rule serves both.

**The same refusals, from one function.** The recall takes the Cell now and not a Swarm node, since a
provider's outage clusters tasks of Cells that are not devices; its rules did not change: a Night
Veil Cell's memory is never recalled, nothing tainted unless a judge cleared it since, nothing
written since the first suspect instant of an isolation that stands, nothing above the task's
clearance, and a refusal is never an error: the task resumes from the Handoff she holds or starts
fresh, and `queen.decided` says why. Each of the three paths is tested with nothing in her own
memory, and with the four refusals planted in turn.

**What is left.** The pause still waits, up to a move's patience, for a Handoff to appear in her
own Bee Bread. A colonized task whose colonization carried none waits out all of it (the bee's
checkpoint is on the device), and one that did carry one stops waiting at once, possibly before the
bee's newest deposit has reached her, in which case the recall sees the deposit before it. Having
the wait recognise a recallable Handoff, or the Warden's own `CHECKPOINTED` progress, would close
both, and is the next step if it shows. The Honey Store still has no taint ledger.

## Amendment (2026-10-27): the pause sees the deposit, and a Virtual Cell that clustered fully is resumed

**The pause waits for a checkpoint it can see.** `pause_on_warden` now notes, before it sends the
`Intervene(HANDOFF)`, the newest Handoff she holds for the task and the newest HANDOFF Nectar the
task's bee deposited from its Cell, and waits for either to change. A colonized device's bee is
seen handing off the moment its deposit lands, instead of waiting out the move's patience, and an
older Handoff she holds no longer ends the wait before the bee has checkpointed. Which of the two
the task resumes from is still the recall's to decide.

**A Warden back from a cut that clustered fully gets its resume.** A Warden whose link stays down
past `max_offline_s` clusters fully and waits for a fresh `TaskAssign`; a device's tasks come back
through the swarm gate's hold, which a Virtual Cell (a Night Veil Cell's Warden redialling Tor) has
no counterpart of, so its tasks sat RUNNING with no bee. A reconnection now marks the Warden
returned (`DispatchBook.returned`), and its first Heartbeat past OFFLINE settles it: CLUSTERED means
every task the chamber has RUNNING on it with no sub-bee row, on a Cell no hold covers, is paused
and resumed through `resume_paused` from what `recall_handoff` returns, with one `queen.decided`
(`resumed_after_cut`) naming them. Any other state ends the check, so a later Heartbeat (one sent
before the Warden read the resume, or the Queen's own Clustering) never resumes a task twice
(`hivemind.queen.cluster.returned`). A Night Veil Cell's Handoffs are never recalled, so its tasks
resume from nothing the Queen holds; the Warden's own store still has the Handoff the bee wrote.
