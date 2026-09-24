# ADR-0032: The Hive Entrance is a FastAPI application on two listeners, in the Queen's process, and the chat is the human end of her inbox

- Status: Accepted
- Date: 2026-09-24

## Context

Everything outside the Hive process reaches the Hive through the Hive Entrance: the Observation
Hive, the `hive` CLI on a laptop, a phone, another program (codingrules 8.15). It must serve
versioned HTTP routes and live WebSocket streams, publish an OpenAPI document generated from its
route models (codingrules 7.7), run two listeners where one of them serves routes the other must
not even have (ADR-0033), and let the human talk to the Queen. Codingrules 2 left the framework
to this ADR ("FastAPI or Starlette"). Until now nothing in the Hive listened for HTTP: `hive run`
keeps a Queen alive only for one goal; a goal carries nothing but its text and clearance and is
planned before anything about it is persisted; the wire already has `control.human_message`
(`HumanMessage`) but the Queen neither reads it nor has a way to reply; and she wakes only when a
Warden link delivers something.

## Decision

**FastAPI on Starlette, served by uvicorn.** FastAPI generates OpenAPI 3.1 from the same pydantic
v2 models the routes validate with, which is the committed-contract requirement for free; plain
Starlette would need the schema hand-assembled. uvicorn serves it with the `websockets` library
the Hive already locks (its sans-I/O protocol). Routes are thin: validate, authorise (ADR-0033),
call a subsystem's public API, shape the reply. Request bodies are JSON pydantic models or, for
audio, a raw body with its media type, so no multipart parser is added. FastAPI's interactive
documentation routes are turned off (they load script from a CDN); the committed document is
served as a static file.

**Two listeners are two applications built from one route table.** Every route module declares
its routes together with the listeners that serve them (`LOOPBACK`, or `LOOPBACK` and `REMOTE`)
and the capability a device must hold, which the route's dependency checks through the guard's
`Entrance route` enforcement point (ADR-0031); routes and streams that return `C2` content (the
chat, thoughts, anything personal) also require `honey:clearance:c2`. `entrance/app.py` builds the
loopback application with every route and the remote application with only the routes whose set
includes `REMOTE`, so a loopback-only route on the remote listener is a 404 because it was never
mounted, never a 403 from a filter someone might reorder. Both run as uvicorn servers inside the
Hive's own event loop, in one `TaskGroup` with the Queen, on sockets the Entrance binds itself and
hands to uvicorn, with uvicorn's own signal handling turned off and a graceful shutdown bounded
to one second. A loopback listener that cannot bind refuses to start `hive serve`, because an
Entrance without its loopback door has no way to be administered. After start, no listener
failure stops the Queen: a remote listener that fails reduces the Entrance and raises an Alarm
(ADR-0033). The remote listener exists only while `expose` is not `loopback` and the Entrance is
not reduced. `hive serve` is the long-running composition root: the Hive `hive run` builds, the
Entrance on top, until interrupted.

**The Entrance lives in the Queen's process and every write goes through her.** It is Layer 7 and
may import `queen`; a second process speaking Waggle to the Queen would add a message and a
failure mode for every route. It also imports its Layer-7 sibling `observation`, through that
package's face only, for the read models its read routes and streams answer with (codingrules
8.11 puts every view's model in `observation/views/`); `observation` never imports the Entrance.
Reads go to the stores directly (Brood Chamber, trail, memory, Forage ledger), because reading
never changes state. Every write goes to the Queen (codingrules
8.11): a goal is submitted to her, an answer is handed to her, a chat message enters her inbox.

**A goal is durable before it is acknowledged.** `POST /v1/goals` writes a `GoalRequest` row in the
Queen's own tables (the text, its budget, its requested Comb Shield tier, its origin, the
submitting device, the device's capability set as the goal's ceiling, and a state) and answers
`202` with its id only after the row is committed. The states are `RECEIVED`,
`AWAITING_CONFIRMATION` (a spoken goal echoed back, or a request held pending a human's step-up,
ADR-0033), `PLANNING`, `PLANNED` (the goal id is set) and `REFUSED` (with a reason). The Entrance
wakes the Queen through an in-process signal she awaits beside her Warden links; she drains the
rows, plans each one herself, and on a restart finds `RECEIVED` and `PLANNING` rows and plans them
again, so a crash after the `202` loses nothing. Planning is therefore never a task the Entrance
owns, and reducing the Entrance cannot kill it. The goal row is what Night Veil placement cites
(ADR-0031), what "goal completed" is pushed to, and what `revoke --cancel-goals` reads.

**The chat is the human end of the Queen's inbox, not a side channel.** A message from
`/v1/chat` becomes a wire `HumanMessage` the Queen receives through the same wake signal, and the
Attendant scores it as `InboxKind.HUMAN_MESSAGE`; autopilot has no rule for free text, so it runs
an awake episode whose trigger carries the message (after the untrusted-content scanner of
roadmap 10.6b), and the decision may be a new `REPLY` action with text. Her replies, her
questions, and Alarms that reached the human are appended to one chat log (`C2`, in the Queen's
own tables) that `/v1/chat` reads and `/v1/chat/stream` streams, so the human sees one
conversation. The trail records that a message arrived and that she replied, never the words.
Audio (roadmap 10.5f) arrives with an intent: `goal` (echoed back and held in
`AWAITING_CONFIRMATION` until `POST /v1/goals/{id}/confirm`), `answer:<question id>` (straight
through, like a typed answer) or `chat`.

**One stream per view, fed from durable state.** `entrance/streams/` holds one WebSocket route per
view (trail events, telemetry per bee, Forage ledger deltas, task graph deltas per principal,
episode records, Cell status, Entrance security events). Each is fed by a `StreamHub` that
follows the trail once (`pheromone.trail.tail.follow`) and fans events out to bounded
per-subscriber queues; a subscriber that falls behind is closed with a reason rather than allowed
to slow the Queen. The same follower is how the Entrance hears a Guard Bee's
`guard.reduce_ordered` (ADR-0035). Telemetry and episode records come from the same hub through
hooks the composition root wires. Clients never poll; the Entrance does the following for them.

**Routes, one module per resource, under `/v1/`:** `goals`, `tasks`, `cells`, `wardens`,
`forage`, `inbox`, `chat`, `episodes`, `tools`, `honey`, `trail`, `swarm`, `llm`, `devices`, plus
`auth`, `enrol` and `entrance` (mode, reduce, open, invites, approvals). A resource whose
subsystem is not built yet (`honey` before phase 7, `tools` before phase 9, `swarm` before phase
11) answers `501` with the phase that fills it, so the contract names it from the start.

## Consequences

Positive: one process, one event loop, one set of stores; routes stay small because the Queen,
the stores and the auth layer do the work; the contract is generated, never hand-written; the
two-application shape makes the loopback-only guarantee structural; a goal the Hive acknowledged
survives a crash. The human's chat reaches the same Attendant, autopilot and awake path as
everything else, so there is no second "human" code path to keep consistent.

Negative: the Entrance shares a process with the Queen, so a route handler's failure must never
take her down; uvicorn isolates handler exceptions, and the Entrance owns its sockets and signals
so uvicorn never exits the process. FastAPI and uvicorn join the dependency set. The Queen gains a
goal-request table, a wake signal, a `REPLY` action and a chat log, all in delegate modules
because `queen.py` is at its size limit.

## Alternatives considered

Starlette alone: the OpenAPI document would be hand-assembled and drift. aiohttp: no
schema-from-models, and a second request-validation idiom beside pydantic. A separate Entrance
process speaking Waggle to the Queen: a new message and a new failure for every route, for no
isolation benefit on one machine. Planning in a task the Entrance owns after answering `202`: a
crash or a reduction loses a goal the Hive already acknowledged. One listener that filters
loopback-only routes by client address: one misconfiguration from exposing approval remotely
(ADR-0033). Polling endpoints for the dashboard: codingrules 8.11 says views subscribe.
