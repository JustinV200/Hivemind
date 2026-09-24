# ADR-0032: The Hive Entrance is a FastAPI application on two listeners, in the Queen's process, and the chat is the human end of her inbox

- Status: Proposed
- Date: 2026-09-24

## Context

Everything outside the Hive process reaches the Hive through the Hive Entrance: the Observation
Hive, the `hive` CLI on a laptop, a phone, another program (codingrules 8.15). It must serve
versioned HTTP routes and live WebSocket streams, publish an OpenAPI document generated from its
route models (codingrules 7.7), run two listeners where one of them serves routes the other must
not even have (ADR-0033), and let the human talk to the Queen. Codingrules 2 left the framework
to this ADR ("FastAPI or Starlette"). Until now nothing in the Hive listened for HTTP: `hive run`
keeps a Queen alive only for one goal, goals carry nothing but their text and clearance, the
wire already has `control.human_message` (`HumanMessage`) but the Queen neither reads it nor has
a way to reply, and she wakes only when a Warden link delivers something.

## Decision

**FastAPI on Starlette, served by uvicorn.** FastAPI generates OpenAPI 3.1 from the same pydantic
v2 models the routes validate with, which is the committed-contract requirement for free; plain
Starlette would need the schema hand-assembled. uvicorn serves it with the `websockets` library
the Hive already locks. Routes are thin: validate, authorise (ADR-0033), call a subsystem's public
API, shape the reply. Request bodies are JSON pydantic models or, for audio, a raw body with its
media type, so no multipart parser is added.

**Two listeners are two applications built from one route table.** Every route module declares
its routes together with the listeners that serve them (`LOOPBACK`, or `LOOPBACK` and `REMOTE`)
and the capability a device must hold. `entrance/app.py` builds the loopback application with
every route and the remote application with only the routes whose set includes `REMOTE`, so a
loopback-only route on the remote listener is a 404 because it was never mounted, never a 403
from a filter someone might reorder. Both run as `uvicorn.Server` instances inside the Hive's own
event loop, in one `TaskGroup` with the Queen; the remote one exists only while `expose` is not
`loopback` and the Entrance is not reduced. `hive serve` is the long-running composition root:
the Hive `hive run` builds, the Entrance on top, until interrupted.

**The Entrance lives in the Queen's process and calls her public API.** It is Layer 7 and may
import `queen`; a second process speaking Waggle to the Queen would add a message and a failure
mode for every route. Reads go to the stores directly (Brood Chamber, trail, memory, Forage
ledger), because reading never changes state. Every write goes through the Queen (codingrules
8.11): a goal is submitted to her, an answer is handed to her, a chat message enters her inbox.
Planning a goal is a model call that can take minutes, so `POST /v1/goals` answers `202` with a
goal request id at once and the Entrance plans it in a task it owns; the request carries the
goal's budget, its origin (the human, with the request id Night Veil placement cites) and the
submitting device, which is how "goal completed" reaches the right device and how `revoke
--cancel-goals` finds a device's goals.

**The chat is the human end of the Queen's inbox, not a side channel.** A message from
`/v1/chat` (or a transcript from `/v1/chat/audio`, roadmap 10.5f) becomes a wire `HumanMessage`
the Queen receives through a human-inbox queue she awaits beside her Warden links, so it wakes
her at once. The Attendant scores it as `InboxKind.HUMAN_MESSAGE`; autopilot has no rule for free
text, so it runs an awake episode whose trigger carries the message (after the untrusted-content
scanner of roadmap 10.6b), and the decision may be a new `REPLY` action with text. Her replies,
her questions, and Alarms that reached the human are appended to one chat log (`C2`, in the
Queen's own tables) that `/v1/chat` reads and `/v1/chat/stream` streams, so the human sees one
conversation. The trail records that a message arrived and that she replied, never the words.

**One stream per view, fed from durable state.** `entrance/streams/` holds one WebSocket route
per view (trail events, telemetry per bee, Forage ledger deltas, task graph deltas per principal,
episode records, Cell status, Entrance security events). Each is fed by a `StreamHub` that
follows the trail once (`pheromone.trail.tail.follow`) and fans events out to bounded
per-subscriber queues; a subscriber that falls behind is closed with a reason rather than
allowed to slow the Queen. Telemetry and episode records come from the same hub through hooks
the composition root wires. Clients never poll; the Entrance does the following for them.

**Routes, one module per resource, under `/v1/`:** `goals`, `tasks`, `cells`, `wardens`,
`forage`, `inbox`, `chat`, `episodes`, `tools`, `honey`, `trail`, `swarm`, `llm`, `devices`, plus
`auth`, `enrol` and `entrance` (mode, reduce, open, invites, approvals). A resource whose
subsystem is not built yet (`tools`, `honey`, `swarm` before phases 7, 9 and 11) answers `501`
with the phase that fills it, so the contract names it from the start.

## Consequences

Positive: one process, one event loop, one set of stores; routes stay small because the Queen,
the stores and the auth layer do the work; the contract is generated, never hand-written; the
two-application shape makes the loopback-only guarantee structural. The human's chat reaches the
same Attendant, autopilot and awake path as everything else, so there is no second "human" code
path to keep consistent.

Negative: the Entrance shares a process with the Queen, so a crash in a route handler must never
take her down (uvicorn isolates handler exceptions; the listeners run under the same `TaskGroup`
and a listener failure stops `hive serve` cleanly rather than leaving a half-open door). FastAPI
and uvicorn join the dependency set. The Queen gains a human-inbox queue, a `REPLY` action and a
chat log, all delegate modules because `queen.py` is at its size limit.

## Alternatives considered

Starlette alone: the OpenAPI document would be hand-assembled and drift. aiohttp: no
schema-from-models, and a second request-validation idiom beside pydantic. A separate Entrance
process speaking Waggle to the Queen: a new message and a new failure for every route, for no
isolation benefit on one machine. One listener that filters loopback-only routes by client
address: one misconfiguration from exposing approval remotely (ADR-0033). Polling endpoints for
the dashboard: codingrules 8.11 says views subscribe.
