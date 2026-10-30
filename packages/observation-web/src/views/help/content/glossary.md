Every bee term the Hive uses, in plain language, in alphabetical order. The screens use these words
literally, so a word on a badge, a tag or a title is the word here.

## Absconding

The human-only emergency shutdown: tear every Virtual Cell down and release every borrowed
device. It needs the password again and a typed phrase, and no screen offers it as one click.

## Access level

How much of a Real Cell the Hive may touch: read only, scratch (only its own working folder) or
full. Set when the device is enrolled. Virtual Cells are always full.

## Alarm

An issue a bee cannot resolve, passed up the chain: sub-bee to Warden to Queen to you. You are
always last.

## Antennae

The virtual keyboard and mouse of the Exoskeleton.

## Apiary (C1)

The middle clearance: internal, not personal.

## Attendant

A supervisor's inbox triage. The Queen's Attendant orders everything waiting on her, weighing your
messages heavily but not absolutely; every Warden has a smaller one. The Attendant screen shows
that order and the task graph.

## Autopilot

Handling an event by fixed rules, with no model call. Every event goes here first, at every level.
The strip counts bees on autopilot as "by rule".

## Awake

A model episode, run only when autopilot cannot decide. It is built from stored state, makes one
decision and is discarded; nothing accumulates. The strip counts these as "by model".

## Basket

The store of files worth keeping that Honey cannot hold: binaries, container images, datasets,
model weights. A file is found through its Honey record, never a listing, and holding one grants no
right to run it.

## Bee

Any agent in the Hive: the Queen, a Warden or a Worker. The strip's Bees count is how many are
working or waiting right now.

## Bee Bread

The warm tier of memory: recent results, episodes and Handoffs, looked up by id rather than
searched.

## Brood

A release of HiveMind (Brood 1.0, Brood 2.0).

## Brood Chamber

The central task store: every goal, task and question, and the state each is in.

## Buzz

The virtual speaker and microphone of the Exoskeleton.

## Capping

The quality gate. Work is proposed with its expected outcome, checked, applied, verified, and
rolled back if the outcome does not hold. Uncapped work never leaves scratch. The Capping screen is
the gate's queue.

## Cell

A machine a Worker runs in or on: either a Virtual Cell the Hive makes or a Real Cell it borrows.
The Fleet lists every one.

## Cell Wax

The Queen's written cautions about one Cell (a known limit, a risk, a quirk), each with a severity:
note, caution or block. Anyone may propose wax; only the Queen writes or clears it. A block keeps
work off the Cell until she clears it.

## Chat

The one place you write. A message talks to the Queen; a goal asks her to plan and run work. Her
replies, questions and escalated Alarms come back on the same screen.

## Clustering

The pause-and-preserve protocol for when a model provider is unavailable: every affected bee
checkpoints, holds, and resumes from its Handoff when the provider returns.

## Colonized

A Real Cell the Hive has moved into: its Warden and sub-bees live on the device itself, with the
tools and Honey they gathered, and stay there between leases.

## Comb Registry

The catalogue of promoted tools, versioned and scoped, that bees may be granted.

## Comb Shield level

A Cell's security tier: Meadow, Propolis or Night Veil. It is a pill badge on every Cell row and
Cell page, and the Fleet's legend says what each level means. A Real Cell's tier is set by you at
enrolment and is never Night Veil; the Queen chooses the tier only for Virtual Cells.

## Compound Eye

The virtual display of the Exoskeleton.

## Cost

What the Hive has spent on hosted models, counted from every model call. The Cost screen shows it
by goal, by slot and by provider, with what moving a slot local would save.

## Device

A phone, laptop or program enrolled at the Hive Stand and approved to use the Landing Board. The
Observation Hive itself runs on one.

## Devices

The screen that lists every enrolled device, what each may do, and the remote-access controls.

## Drone

A short-lived, general-purpose Worker for one-off tasks.

## Enrol

Letting a new device in: an invite minted on the Hive Stand, then approval once the device joins.
The Enrol screen exists only on the Hive Stand.

## Entrance Reducer

The emergency narrowing of the Hive Entrance: drop to the Hive Stand only and end every remote
session. Only the Hive Stand reopens it.

## Episode

One awake run of a bee: what it saw, what it decided and why. The Thoughts screen lists them.

## Exoskeleton

The virtual peripherals a Worker can be given on demand, on any Cell: a display (Compound Eye),
keyboard and mouse (Antennae) and audio (Buzz).

## Fanner

The meter every model call passes through: it enforces seat counts, queues by tempo, measures
speed and reports usage.

## Fleet

Every Cell the Hive works on, Real and Virtual. The Fleet screen is the list.

## Forage

Capacity: cores, memory, GPU, model seats and spend. Cells report it, Wardens hold grants of it,
and only the Queen divides what is shared.

## Forage map

The catalogue of every source that can serve a model, with its grade (strength), distance
(latency from a given Cell), abundance (free seats) and cost.

## Forager

A Worker that gathers data from outside: scraping, search, monitoring.

## Goal

Work you ask the Queen for from the chat. She plans it into tasks and may ask you to confirm it
first.

## Grant

What a Warden holds of the shared Forage: which models it may use, how many seats, and its token
and spend budgets. A grant is a lease, renewed on the Warden's heartbeat and returned to the pool
when the Warden stops.

## Guard Bee

The Worker that watches for security trouble: denial bursts, writes outside scratch, suspected
prompt injection, failures at the door. It files requests; only the Queen isolates a Cell.

## Handoff

The structured note a bee writes before its context is reset, rebound, moved or paused, so the next
bee resumes from it without the transcript.

## Help

This screen: the words, how the Hive works, how to read each screen, and the tour.

## Hive Entrance

The one door into the Hive: the authenticated HTTP and WebSocket gateway every client uses. It has
two listeners: loopback, always on, and remote, only when the Hive is exposed.

## Hive Manifest

The Hive's configuration file.

## Hive Stand

The machine HiveMind runs on: home of the Queen, the first Real Cell, and where every Warden lives
by default. A role rather than a fixed machine, since Supersedure can move it.

## Honey

Distilled, indexed knowledge in the Honey Store, retrieved by search. The Honey screen is the
browser.

## Honey clearance

A sensitivity label on every piece of memory: Wildflower (C0) public, Apiary (C1) internal, Royal
(C2) personal. Anything about you, down to a first name or a habit, is Royal.

## Honey Store

The persistent, ever-growing knowledge base: the cold tier of memory.

## House Bee

The Worker that keeps memory tidy: it sweeps stale hot state into Bee Bread and ripens Nectar and
Bee Bread into Honey.

## Invite

A short-lived, single-use code minted on the Hive Stand that lets one device enrol.

## Judge

A model on its own slot, with no shared context with the bee it checks, that reviews riskier
proposals and the sampled audits.

## Landing Board

The Hive Entrance's public, versioned API. Only devices enrolled and approved at the Hive Stand
may use it; this app is one of them.

## Lease

The Hive's borrowing of a Real Cell for a task: a scratch folder, the processes it started, and the
promise to leave the device exactly as found.

## Meadow

Comb Shield tier 0: the default baseline, any machine, no VPN required.

## Model seat

One request at a time on one model on one server. Seats are the resource that runs out first, so
they are counted, never estimated.

## Monarch

The Queen's name in the chat: what she calls herself when speaking to you.

## Nectar

Raw findings a Worker brings back from a task, before they are ripened into Honey.

## Night Veil

Comb Shield tier 2: Virtual Cells only, asked for by a human, every connection through a VPN and
Tor, local models only, and torn down the moment the task ends.

## Nuc

A colonized Real Cell that also runs its own model server, so it keeps working when cut off from
the Hive Stand.

## Nurse Bee

A reviewer with no share in a Worker's context. It reads the evidence of a finished attempt and
says pass, or what to fix. It runs on the judge slot, on a model at least as strong as the Worker's.

## Observation Hive

This app: the live, read-only window onto the Hive, with the chat as its one write.

## Overwintering

Keeping idle Virtual Cells dormant rather than destroyed, so the next task on that image starts
fast. Night Veil Cells are never overwintered.

## Patrol

The scheduled review a watching Warden runs over what it observed: raise an Alarm, file a summary,
propose Cell Wax, or discard it.

## Pheromone Mask

A short-lived behaviour overlay for fragile flows: Write-Like-Human (tone and pacing) and
Mouse-Like-Human (input cadence). The badge says OFF, WARDEN (a Warden set it for one task
segment) or QUEEN FORCED (the Queen set it for the whole Cell, with an expiry).

## Pheromone Trail

The audit trail of everything the Hive did. Every state change is an event, written before the
action counts as done.

## Pollen Packet

The thin gateway installed on an external device: it enrols the device and opens a terminal session
for the device's Warden. It has no brain of its own.

## Propolis

Comb Shield tier 1: hardened for normal production work, a VPN required and Tor disabled.

## Proposal

One change a bee wants to make outside scratch, stated with its risk tier and the outcome it
expects, for the Capping gate to check.

## Quarantine Comb

The sandbox a new tool must pass through before it is promoted into the Comb Registry.

## Queen

The central orchestrator and the only bee with a global view. She owns the task graph, divides
Forage, assigns work to Wardens, and never does hands-on work herself.

## Real Cell

An existing device (the Hive Stand, or a device in the Swarm) borrowed for a task and left exactly
as found.

## Requeening

Recovering from a crashed Queen: she rebuilds from the Brood Chamber and the Pheromone Trail and
reconciles with every Cell and Warden.

## Ripening

Turning Nectar into Honey: chunking, summarising, embedding and indexing.

## Royal (C2)

The highest clearance: personal or sensitive. Any detail about you, down to a first name or a
habit, is Royal.

## Royal Jelly Lab

The pipeline that authors and validates a new tool before the Comb Registry takes it.

## Royal Reserve

Forage held back before any grant: what the Queen, the Attendant and the House Bees need, plus
headroom.

## Scout

A Worker that explores a new target before real resources are committed to it.

## Scratch

A lease's own working folder on a Cell. Writes inside it are free; anything outside it needs a
capability and goes through Capping.

## Slot

The role a model is asked for: queen, warden, worker, attendant, judge, ripener, embedder,
transcriber. The manifest maps each slot to a provider and a model, so nothing in the Hive names a
model directly.

## Stand key

A key drawn each time the Entrance starts, kept in memory on the Hive Stand and never given to a
Cell. The routes that admit or empower a device need it, so a Virtual Cell that can reach the
loopback listener still cannot approve anything.

## Step-up

Confirming again, with the password and the device key, before a sensitive action: spend above a
cap, device changes, Supersedure, Absconding.

## Sting Cut

A per-Cell emergency disconnect: revoke the lease, end the Hive's processes there and scrub
lease-local traces, keeping the central audit.

## Supersedure

Moving the Hive Stand to another machine, started by a human, with never two Queens running.

## Swarm

The mesh of external devices enrolled with the Queen as Real Cells.

## Swarming

Scaling up: more Cells and Workers, within Forage.

## Task

One unit of work in a goal's plan, assigned to a Warden and run by a Worker on a Cell.

## Tempo

A task's speed-against-accuracy setting: a latency budget and an accuracy bar. Read when choosing
models, dividing Forage and deciding how much checking a task gets.

## Thinking

The strip's count of bees in awake episodes (by model) beside bees handled by autopilot (by rule).

## Thoughts

A bee's episodes: what it decided and why, newest first. The Thoughts screen shows them for the
Queen or any bee.

## Undertaker

The Worker that cleans up: it tears down finished Virtual Cells and releases borrowed Real Cells.

## Virtual Cell

A fresh VM or container the Hive provisions on demand and tears down, or overwinters, when the task
ends.

## Waggle

The protocol bees use to talk to one another. Model calls travel separately, to whichever provider
serves the model.

## Warden

The always-on supervisor of one Cell: it spawns and watches sub-bees, handles their Alarms,
escalates what it cannot resolve, and never provisions Cells.

## Watch mode

What a Real Cell's Warden does with no bees active: observe read-only, and on each Patrol review
what it saw.

## Watch request

A task's ask to see and use your own screen, or to hear and play sound, on a machine that has
them (your screen only where its manifest allows it). The Queen holds the task until you answer on
the [Enrol screen](#/enrol) at the Hive Stand: Allow lets it start, Refuse cancels it, and one
unanswered for fifteen minutes is refused. No goal or planner can grant either on its own.

## Wildflower (C0)

The lowest clearance: public, nothing sensitive.

## Worker

A subagent bound to one Cell, spawned and supervised by that Cell's Warden. The roles are Forager,
Scout, Guard Bee, Undertaker, Drone and House Bee.
