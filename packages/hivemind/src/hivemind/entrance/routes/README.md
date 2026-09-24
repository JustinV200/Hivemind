# hivemind.entrance.routes

One module per resource of the Landing Board (the Hive Entrance's committed contract, ADR-0032),
each declaring its rows as `RouteSpec`s: method, path, the listeners that serve it, who may call it
(`Access`) and what it changes (`RouteEffect`). `registry.RESOURCE_ROUTES` lists every resource;
`hivemind.entrance.app.route_table` builds both listeners' applications and the OpenAPI document
from it. Adding a resource is one module and one line in the registry. Routes are thin: validate,
authorise (the gate does it from the row), call a subsystem's public API, shape the reply; every
write into the Hive goes through the Queen's door.

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
| GET | /v1/inbox | L+R | entrance:answer +c2 | read |
| POST | /v1/inbox/questions/{question_id}/answer | L+R | entrance:answer | inbox |
| POST | /v1/inbox/alarms/{alarm_id}/acknowledge | L+R | entrance:answer | inbox |
| POST | /v1/push/subscriptions | L+R | entrance:push | push |
| DELETE | /v1/push/subscriptions/{subscription_id} | L+R | entrance:push | push |
| GET | /v1/push/vapid-key | L+R | entrance:push | read |
| GET | /v1/push/hive-key | L+R | entrance:push | read |

The WebSocket views (`hivemind.entrance.streams`, first frame within five seconds):
`/v1/chat/stream` (entrance:submit +c2), `/v1/push/stream` (entrance:push),
`/v1/entrance/stream` (observe), all on both listeners. Each listener also serves
`GET /v1/openapi.json` (the committed document, unauthenticated) and, when it has been built, the
Observation Hive's front end.

## How to test this

`tests/unit/entrance/test_app.py` walks the table (every row declares its listeners and access,
no loopback-only row is served remotely, the Observation Hive's credentials reach no mutating route
beyond the Queen's inbox, their own session and push); `tests/unit/entrance/routes/` exercises the
resources over a real listener; `tests/unit/entrance/test_landing_board.py` fails when the committed
document drifts from this table.
