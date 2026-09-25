# hivemind.entrance.routes

One module per resource of the Landing Board (the Hive Entrance's committed contract, ADR-0040),
each declaring its rows as `RouteSpec`s: method, path, the listeners that serve it, who may call it
(`Access`) and what it changes (`RouteEffect`). `registry.RESOURCE_ROUTES` lists every resource;
`hivemind.entrance.app.route_table` builds both listeners' applications and the OpenAPI document
from it. Adding a resource is one module and one line in the registry. Routes are thin: validate,
authorise (the gate does it from the row), call a subsystem's public API, shape the reply; every
write into the Hive goes through the Queen's door, and every read goes straight to the stores and
the Queen's live tables (`gate.HiveReads`). `hive/` holds the Hive's read routes (tasks, Cells,
Wardens, Forage, episodes, trail, LLM); `later/` holds the resources a later phase fills, each
answering 501 with the phase that builds it. `POST /v1/chat/audio` is declared with the rest of
voice, in `hivemind.entrance.voice`, and listed in the registry beside the chat.

## The route table

`L` = loopback only (a 404 on the remote listener, because it is never mounted); `L+R` = both.
Capabilities are checked at the Guard's `ENTRANCE_ROUTE` enforcement point; `+c2` also needs
`honey:clearance:c2` (the route returns personal content); "session" means any approved device's
own session.

| Method | Path | Listeners | Access | Effect |
|---|---|---|---|---|
| POST | /v1/auth/challenge | L+R | public | session |
| POST | /v1/auth/login | L+R | public | session |
| POST | /v1/auth/logout | L+R | session | session |
| POST | /v1/auth/step-up/challenge | L+R | session | session |
| POST | /v1/auth/step-up | L+R | session | session |
| GET | /v1/enrol/hive | L+R | public | read |
| POST | /v1/enrol/passkey-options | L+R | public | session |
| POST | /v1/enrol/ed25519 | L+R | public | session |
| POST | /v1/enrol/passkey | L+R | public | session |
| GET | /v1/entrance/mode | L+R | observe | read |
| POST | /v1/entrance/reduce | L+R | entrance:steward | door |
| POST | /v1/entrance/open | L | entrance:steward, step-up | door |
| POST | /v1/entrance/invites | L | entrance:steward | door |
| DELETE | /v1/entrance/invites/{device_id} | L | entrance:steward | door |
| GET | /v1/entrance/pending | L+R | entrance:steward | read |
| POST | /v1/entrance/pending/{device_id}/approve | L | entrance:steward | door |
| POST | /v1/entrance/pending/{device_id}/deny | L | entrance:steward | door |
| POST | /v1/entrance/steward/{device_id}/approve | L+R, only with `steward_devices` | entrance:steward, step-up | door |
| POST | /v1/entrance/operators | L | entrance:steward | door (refused: one operator) |
| GET | /v1/entrance/confirmations | L+R | entrance:submit +c2 | read |
| POST | /v1/entrance/confirmations/{pending_id}/confirm | L+R | entrance:submit, step-up | door |
| POST | /v1/entrance/confirmations/{pending_id}/cancel | L+R | entrance:submit | door |
| GET | /v1/devices | L+R | observe | read |
| GET | /v1/devices/me | L+R | session | read |
| POST | /v1/devices/{device_id}/lock | L+R | entrance:submit, step-up | door |
| POST | /v1/devices/{device_id}/unlock | L | entrance:steward | door |
| POST | /v1/devices/{device_id}/revoke | L | entrance:steward | door |
| POST | /v1/devices/{device_id}/capabilities | L | entrance:steward, step-up | door |
| POST | /v1/goals | L+R | entrance:submit (step-up above `step_up_spend` or the daily cap) | inbox |
| GET | /v1/goals/{request_id} | L+R | entrance:submit | read |
| POST | /v1/goals/{request_id}/confirm | L+R | entrance:submit | inbox |
| POST | /v1/goals/{request_id}/decline | L+R | entrance:submit | inbox |
| GET | /v1/chat | L+R | entrance:submit +c2 | read |
| POST | /v1/chat | L+R | entrance:submit | inbox |
| POST | /v1/chat/audio | L+R, only with `[entrance.voice] enabled` | entrance:submit +c2 (an answer also entrance:answer; a goal steps up as `/v1/goals` does) | inbox |
| GET | /v1/inbox | L+R | entrance:answer +c2 | read |
| POST | /v1/inbox/questions/{question_id}/answer | L+R | entrance:answer | inbox |
| POST | /v1/inbox/alarms/{alarm_id}/acknowledge | L+R | entrance:answer | inbox |
| POST | /v1/push/subscriptions | L+R | entrance:push | push |
| DELETE | /v1/push/subscriptions/{subscription_id} | L+R | entrance:push | push |
| GET | /v1/push/vapid-key | L+R | entrance:push | read |
| GET | /v1/push/hive-key | L+R | entrance:push | read |
| GET | /v1/tasks | L+R | observe | read |
| GET | /v1/tasks/{task_id} | L+R | observe | read |
| GET | /v1/tasks/{task_id}/brief | L+R | observe +c2 | read |
| GET | /v1/cells | L+R | observe | read |
| GET | /v1/cells/{cell_id} | L+R | observe | read |
| GET | /v1/wardens | L+R | observe | read |
| GET | /v1/forage | L+R | observe | read |
| GET | /v1/episodes | L+R | observe:thoughts +c2 | read |
| GET | /v1/trail | L+R | observe | read |
| GET | /v1/llm | L+R | observe | read |
| GET | /v1/tools | L+R | observe (501 until phase 9) | read |
| GET | /v1/honey | L+R | observe (501 until phase 7) | read |
| GET | /v1/swarm | L+R | observe (501 until phase 11) | read |

`GET /v1/tasks` pages by keyset (`goal_id`, `status`, `after` = the previous page's `next_after`,
`limit` up to 500) and answers no task's words; `/brief` holds them. `GET /v1/trail` takes
`family`, `kind`, `subject_id`, `newest_first`, `limit` (up to 500) and the cursor a page's `next`
names (`since` or `until`, and `skip`, the events at exactly that instant already read); an unknown
query parameter is a 422. `GET /v1/episodes` answers the newest records first, optionally one
bee's (`principal`), up to `limit`.

The WebSocket views (`hivemind.entrance.streams.views`, first frame within five seconds), all on
both listeners, each fed from durable state and closed with FELL_BEHIND past its backlog; the
OpenAPI document lists them under `x-hive-streams`:

| Path | Access | What each frame carries |
|---|---|---|
| /v1/chat/stream | entrance:submit +c2 | a chat line as it is written; it also takes push-to-talk (`audio_chunk` frames, then `audio_end`) and answers the speaking socket alone (`voice` or `voice_refused`) |
| /v1/push/stream | entrance:push | a content-free push notice |
| /v1/entrance/stream | observe | an Entrance security event |
| /v1/trail/stream | observe | a trail event (`family`, `kind` filters) |
| /v1/telemetry/stream | observe:thoughts +c2 | one bee's sample from a Heartbeat (`warden_id` filter) |
| /v1/forage/stream | observe | a `forage.*` event, its grant now, the headroom after it |
| /v1/tasks/stream | observe | a changed task, no words, in one principal's graph (`principal`: queen or a Warden's id) |
| /v1/episodes/stream | observe:thoughts +c2 | a new episode record (`principal` filter) |
| /v1/cells/stream | observe | every Cell once, then each Cell whose status changed |

Each listener also serves `GET /v1/openapi.json` (the committed document, unauthenticated) and,
when it has been built, the Observation Hive's front end.

## How to test this

`tests/unit/entrance/test_app.py` walks the table (every row declares its listeners and access,
no loopback-only row is served remotely, the Observation Hive's credentials reach no mutating route
beyond the Queen's inbox, their own session and push); `tests/unit/entrance/routes/` exercises the
resources over a real listener (`hive/` and `later/` mirror the read routes);
`tests/unit/entrance/streams/views/` opens every live view; `tests/unit/entrance/voice/` speaks
to the audio route and the chat socket; `tests/unit/entrance/test_landing_board.py` fails when the
committed document drifts from this table.
