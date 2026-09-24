# hivemind.entrance

The Hive Entrance is the only door into the Hive: two listeners, loopback (always on) and remote
(only when explicitly exposed), serving the Landing Board (the versioned public API contract),
device enrolment, auth, push delivery, exposure control and the human inbox. Approval routes never
exist on the remote listener. Every client, the Hive Stand's own console included, is a device
enrolled with its own key and approved at the Hive Stand, and logs in with that key plus the
operator's password (codingrules 8.15, `docs/adr/0033-landing-board-enrolment-two-factor-login-and-exposure.md`).
It runs inside `hive serve`, in the Queen's own process and event loop (ADR-0032), and every write
it makes into the Hive goes through the Queen's door.

Roadmap steps 10.4, 10.5a (remote exposure), 10.5b (push), 10.5d (device enrolment), 10.5e
(login, sessions, step-up and the Entrance Reducer) and 10.5 (the application, its routes, streams
and runtime, `hive serve`) landed what is below.

## Layout

| Package | What it holds |
|---|---|
| `errors.py` | `EntranceError` and every refusal, each also in its `hivemind.common.errors` category. |
| `auth/` | Credential primitives (the signed strings, device keys, networks, Argon2id passwords, the wrapped console key, WebAuthn, the `ChallengeBook`, `SoftPasskey`) and the flows built on them: `session/` (sessions bound to a key, every request signed, a socket's first frame, `guard.entrance_login` and `guard.entrance_session_ended`), `login/`, `step_up/`, `confirm/` (pending confirmations and their `guard.entrance_held` / `_confirmed` / `_hold_ended` events), `limits/` (rate limits, the denial-burst lock), `travel/` (the travel lock over tailscaled). |
| `enrol/` | Device enrolment: the enrolled-device model and its state machine, the operator/console bootstrap, invites, redemption by Ed25519 key or passkey, approval, denial and re-granting, revocation, lock, unlock and the expiry sweep, and the seams the runtime implements (`SecurityNotifier`, `DeviceOffboarder`, `GoalLedger`). |
| `reducer.py` | The Entrance Reducer: `EntranceMode` (`OPEN`, `REDUCED`, persisted), `reduce` (ends every remote session, tells every remote socket why, stops the remote listener), `reopen` (loopback only, after step-up), `start_mode`. |
| `store/` | The Entrance tables; every change is written with its `guard.*` trail event in one transaction. |
| `push/` | The push channels (webhooks, Web Push, the live socket hub, the dispatcher); its own README. |
| `expose/` | Remote exposure: the pure mode check, the Hive's certificate authority and the mutual-TLS context, the tunnel client, the loopback Host check; its own README. |
| `gate/` | What every request passes: the route table's rows (`spec`), the ASGI wrappers (`middleware`: security headers, the loopback check, the per-address limit, the body limit), admission (`admit`: the signed request, the per-device limit, the travel lock, each capability at `EnforcementPoint.ENTRANCE_ROUTE`), step-up, the error handlers, and the services a route is handed. |
| `models/` | The Landing Board's request and response models, one module per resource. |
| `routes/` | One module per resource, each declaring its rows; `registry` lists them; its own README holds the route table. |
| `streams/` | The live views: `StreamHub` (one trail follower, bounded queues), the socket registry and lifecycle, the chat, push and security views, and the follower obeying a Guard Bee's `guard.reduce_ordered`. |
| `notify/` | `PushOutbox` (notices off the caller's path, ordered per ref), `PushHumanChannel` (the Queen's `HumanChannel`), `PushSecurityNotifier`, `HumanChannelRelay`. |
| `app.py` | Builds both FastAPI applications from one route table (the remote one mounts only `REMOTE` rows). |
| `landing_board.py` | Generates the OpenAPI document (`docs/entrance/openapi.json`) from the same table. |
| `runtime/` | `build_entrance` (the wiring), `HiveEntrance` (start-up checks, both listeners, background work, a clean stop), the listeners on uvicorn, the TLS context, the offboarder and goal ledger. |

## How a request travels

```
socket (bound by the Entrance) ── uvicorn, in the Hive's loop
   │
   ▼
SecurityHeaders ─► LoopbackGate (loopback only: Host, no forwarding header; bare 403)
   │                 ─► AddressLimit (per address) ─► BodyLimit ─► FastAPI (CORS: public_url, remote only)
   ▼
route row ── public? ──────────────────────────────► endpoint
   │ authenticated
   ▼
gate_for(access): authenticate_request (token + binding-key signature over the request exactly as
sent, fresh timestamp, single-use nonce, the listener it was opened on, an APPROVED device)
   ─► per-device limit ─► travel lock ─► Enforcer.check at ENTRANCE_ROUTE (a denial: guard.denied,
      and a count toward the burst lock) ─► honey:clearance:c2 for personal content
   ▼
endpoint ── writes through the Queen's door (request_goal, post_human_message, answer_question, ...)
         └─ or an Entrance flow (invite, approve, lock, revoke, reduce, hold, confirm)
```

A WebSocket view authenticates its first frame (within five seconds) the same way, then races the
view, the client, the socket registry (a logout, a lock, a revocation or a reduction closes it with
its reason) and a session watchdog.

## Public API

The face (`hivemind.entrance`) re-exports the credential primitives, the enrolled-device model,
enrolment, the tables, login and sessions, and the Reducer, as before; the application and the
runtime are reached through their own modules, so importing the face never loads FastAPI:

- `hivemind.entrance.runtime`: `EntranceParts` (and its `EntranceSettings`, `EntranceTables`,
  `EntranceHive`, `EntranceKeys`), `build_entrance`, `BuiltEntrance`, `HiveEntrance`,
  `EntranceListeners`, `ListenerServer`, `bind_listener`, `RemoteTls`.
- `hivemind.entrance.app`: `route_table`, `build_listener_app`, `ListenerOptions`, `OPENAPI_PATH`.
- `hivemind.entrance.landing_board`: `openapi_document`, `render_document`, `write_document`,
  `DOCUMENT_PATH`.
- `hivemind.entrance.gate`, `hivemind.entrance.streams`, `hivemind.entrance.notify`: their faces
  list every name.

Nothing in the Entrance stores a password, an invite code, a session token or a private key in
the clear, and no log line or trail event carries a code, a key, a token, a signature, a password
or a human's words.

## How to test this

```bash
uv run --frozen pytest packages/hivemind/tests/unit/entrance \
    packages/hivemind/tests/contracts/test_entrance_store_contract.py \
    packages/hivemind/tests/contracts/test_entrance_grant_contract.py \
    packages/hivemind/tests/contracts/test_entrance_session_table_contract.py \
    packages/hivemind/tests/contracts/test_entrance_auth_tables_contract.py \
    packages/hivemind/tests/unit/cli/compose/test_entrance.py \
    packages/hivemind/tests/unit/cli/test_serve.py \
    packages/hivemind/tests/e2e/test_hive_serve.py
```

The route, gate, stream and runtime tests run a real Entrance (`builders.entrance.serving`): uvicorn
on loopback ports over a real Queen, push deliveries to a recording fake push service, and a device
client (`builders.entrance.landing`) that enrols (Ed25519 or passkey), logs in and signs exactly as a
program or a browser does. `tests/e2e/test_hive_serve.py` runs `hive serve`'s own composition over
the Hive's SQLite file with a scripted provider: the console approves a program, which submits a
goal the Hive finishes and reads the Queen's reply. After changing a route or a model, run
`uv run --frozen python scripts/write_landing_board.py` and commit `docs/entrance/openapi.json`;
the drift test fails until you do.
