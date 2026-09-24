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
(login, sessions, step-up and the Entrance Reducer), 10.5 (the application, its routes, the
Hive's read routes and live views, and the runtime, `hive serve`) and 10.5f (voice in at the
Landing Board) landed what is below.

## Layout

| Package | What it holds |
|---|---|
| `errors.py` | `EntranceError` and every refusal, each also in its `hivemind.common.errors` category. |
| `auth/` | Credential primitives (the signed strings, device keys, networks, Argon2id passwords, the wrapped console key, WebAuthn, the `ChallengeBook`, `SoftPasskey`) and the flows built on them: `session/` (sessions bound to a key, every request signed, a socket's first frame, `guard.entrance_login` and `guard.entrance_session_ended`), `login/`, `step_up/`, `confirm/` (pending confirmations and their `guard.entrance_held` / `_confirmed` / `_hold_ended` events), `limits/` (rate limits, the denial-burst lock), `travel/` (the travel lock over tailscaled). |
| `enrol/` | Device enrolment: the enrolled-device model and its state machine, the operator/console bootstrap, invites, redemption by Ed25519 key or passkey, offline registration by the operator, approval (with the device's mutual-TLS certificate, `certificates`), denial and re-granting, revocation, lock, unlock and the expiry sweep, and the seams the runtime implements (`SecurityNotifier`, `DeviceOffboarder`, `GoalLedger`, and the `DeviceCertifier` the composition root builds). |
| `reducer.py` | The Entrance Reducer: `EntranceMode` (`OPEN`, `REDUCED`, persisted), `reduce` (ends every remote session, tells every remote socket why, stops the remote listener), `reopen` (loopback only, after step-up), `start_mode`. |
| `store/` | The Entrance tables; every change is written with its `guard.*` trail event in one transaction. |
| `push/` | The push channels (webhooks, Web Push, the live socket hub, the dispatcher); its own README. |
| `expose/` | Remote exposure: the pure mode check, the Hive's certificate authority and the mutual-TLS context, the tunnel client, the loopback Host check; its own README. |
| `gate/` | What every request passes: the route table's rows (`spec`), the ASGI wrappers (`middleware`: security headers, the loopback check, the per-address limit, the body limit, with a raw-body row's own allowance), admission (`admit`: the signed request, the per-device limit, the travel lock, each capability at `EnforcementPoint.ENTRANCE_ROUTE`), step-up, the error handlers, the services a route is handed, and `reads` (`HiveReads`: the stores and live tables the Entrance reads directly). |
| `models/` | The Landing Board's request and response models, one module per resource; `views/` holds the Hive's read models (tasks, Cells, Wardens, Forage, episodes, trail, LLM, the not-built answer) and every live view's frame. |
| `reads/` | The read side the routes and views share: `census` (every Cell and Warden, joined from the Warden links, the Virtual Cell lifecycle, the trail, the Brood Chamber, the Queen's pulse, the telemetry board and the ledger), `trail` (a filtered page from a cursor), `llm` (providers as the Queen judges them, and every binding). |
| `routes/` | One module per resource, each declaring its rows; `hive/` holds the Hive's read routes, `later/` the resources a later phase fills (answering 501); `registry` lists them; its own README holds the route table. |
| `intake.py` | How every goal comes in, typed, spoken, confirmed or recovered: its spend weighed against its device's day, and a held goal committed once under the id minted when it was held. |
| `voice/` | Voice in: a clip on `POST /v1/chat/audio` or a push-to-talk hold on the chat socket, both heard at one door (every refusal before the model, one transcription on `TRANSCRIBER`, the scan, then where typed words go); the `AudioNectar` seam for `keep_audio`; its own README. |
| `streams/` | The live views: `StreamHub` (one trail follower), `StreamSubscription` (a bounded queue, closed with FELL_BEHIND past its backlog), `TelemetryBoard` (every Heartbeat the Queen hands its `on_heartbeat` hook), the socket registry and lifecycle, `views/` (the Landing Board's chat, push and security views, and the Hive's trail, telemetry, Forage, task-graph, episode and Cell-status views), and the follower obeying a Guard Bee's `guard.reduce_ordered`. |
| `notify/` | `PushOutbox` (notices off the caller's path, ordered per ref), `PushHumanChannel` (the Queen's `HumanChannel`), `PushSecurityNotifier`, `HumanChannelRelay`. |
| `app.py` | Builds both FastAPI applications from one route table (the remote one mounts only `REMOTE` rows). |
| `landing_board.py` | Generates the OpenAPI document (`docs/entrance/openapi.json`) from the same table. |
| `runtime/` | `build_entrance` (the wiring), `HiveEntrance` (start-up checks, both listeners, background work, a clean stop), the listeners on uvicorn (the loopback one restarted with backoff when it fails), the TLS context, the offboarder and goal ledger, and `recovery` (a confirmed goal a crash kept from the Queen, committed on start). |

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

## Reads and live views

Reads never change state, so the read routes and the live views read the Hive's stores and the
Queen's live tables directly (`gate.HiveReads`), never through the Queen: the Brood Chamber, the
Pheromone Trail, memory, the Queen's goal-request table and chat log, her Forage ledger, her
attached Wardens with their pulse (`HiveCensus`, which the Queen satisfies with read-only
properties), the provider bindings with her Clustering state and health poller, and the Virtual
Cell lifecycle's table. What is personal is decided per field: a task's title, objective,
criteria, summaries and artifact paths are C2 (written from the human's goal), so the task views
carry none of them and `GET /v1/tasks/{id}/brief` answers them behind `honey:clearance:c2`; the
trail views leave out the title `task.submitted` records for the same reason; episode records and
telemetry samples are a bee's own words, behind `observe:thoughts` and C2. No view carries an API
key, a key's variable or a base URL.

Every live view is a WebSocket fed by one follower of the trail (`StreamHub`) through a bounded
subscription; a reader that falls further behind than the Entrance's stream backlog (512 items)
is closed with FELL_BEHIND (4409) and reconnects from its cursor, so a slow client never slows the
feed. The one feed not on the trail is telemetry: Heartbeats never reach it, so the composition
root sets the Queen's `on_heartbeat` hook to a `TelemetryBoard`, which keeps each Warden's newest
Heartbeat (the Wardens read counts its sub-bees from it) and fans every one out the same way.

The view models live in `hivemind.observation.views` (codingrules 8.11), and the Entrance, which
answers with them and publishes them in the OpenAPI document, imports them from the
`hivemind.observation` face only: the import-linter ranks `entrance` over `observation` and holds
the edge to the face (ADR-0032 lists it); nothing in `observation` imports the Entrance.

## When a listener fails

A listener that fails after start never stops the Queen. The remote one reduces the Entrance to
loopback and raises an Alarm (reopening is a loopback action, after step-up). The loopback one is
the only door the Hive is administered through, so it is rebound on the same address and served
again, half a second after the failure and doubling to at most thirty seconds while it keeps
failing, with one Alarm per outage.

## Rate limits in tunnel mode

In `tunnel` mode the remote listener binds loopback and only the supervised tunnel client reaches
it. A TCP tunnel carries no client address (and forwarding headers are never trusted), so the
per-address limit sees every remote request as coming from the tunnel's loopback address: it bounds
the tunnel as a whole, not each client behind it. The per-device limit still holds for every
device, since it is charged once a request is authenticated to its device, whatever address it
arrived from.

## Device certificates (mutual TLS)

`[entrance] mutual_tls` follows the exposure mode when the manifest leaves it out. It is on in
`lan` and `tunnel`, which refuse to start with it off, and off in `vpn` unless the operator turns
it on. Loopback has no remote listener, so there it changes nothing. In every remote mode the Hive
runs its own certificate authority (in the secret store), and approval issues each device its
client certificate from it:

- **A program** sends a PKCS#10 request for a key it holds: `certificate_request` in its Ed25519
  redemption, or in the operator's offline registration (`POST /v1/entrance/register`,
  loopback-only, `hive entrance register`), for a device that can reach no enrolment listener.
  The request is checked when it arrives: a damaged one is refused and writes nothing. Approval
  signs it, whether or not the listener demands certificates, so a device approved under `vpn`
  keeps working if mutual TLS is turned on later. The device reads its certificate with
  `GET /v1/devices/me/certificate`, or the operator writes it out (`approve --certificate-out`)
  for an offline device.
- **A browser** cannot make a request. Under mutual TLS, the loopback approval route alone seals a
  fresh key and its certificate into a PKCS#12 bundle, answered once to the operator with its
  passphrase. `hive entrance approve` writes it owner-only and prints the passphrase once. The
  bundle is never stored.

The device's record keeps the certificate itself, which is public, together with its serial,
fingerprint and expiry. The approval's trail event names only the serial and fingerprint.
Revocation, expiry and a console reset stamp the certificate withdrawn in the same step that moves
the device. The revocation list is built from the Entrance tables whenever the remote listener's
TLS context is built: at start, after every revocation, and after an expiry sweep that expired
anything. It names every certificate of a device that is no longer APPROVED or LOCKED. It fails
closed, so a certificate that was never stamped is still listed, dated at its approval. The rebuilt
context reaches the next handshake through the listener's SNI callback, without a restart.

## Public API

The face (`hivemind.entrance`) re-exports the credential primitives, the enrolled-device model,
enrolment, the tables, login and sessions, and the Reducer, as before; the application and the
runtime are reached through their own modules, so importing the face never loads FastAPI:

- `hivemind.entrance.runtime`: `EntranceParts` (and its `EntranceSettings`, `EntranceTables`,
  `EntranceHive`, `EntranceKeys`), `build_entrance`, `BuiltEntrance`, `HiveEntrance`,
  `EntranceListeners`, `ListenerServer`, `bind_listener`, `RemoteTls`, `submit_confirmed_goals`.
- `hivemind.entrance.gate`: besides the gate, `HiveReads`, `HiveCensus`, `LlmReads` and
  `VirtualCellCensus`, what the composition root hands the read side.
- `hivemind.entrance.app`: `route_table`, `build_listener_app`, `ListenerOptions`, `OPENAPI_PATH`.
- `hivemind.entrance.landing_board`: `openapi_document`, `render_document`, `write_document`,
  `DOCUMENT_PATH`.
- `hivemind.entrance.streams` (with `TelemetryBoard`), `hivemind.entrance.notify`,
  `hivemind.entrance.reads`: their faces list every name. The view models are
  `hivemind.observation`'s.
- `hivemind.entrance.voice`: `VoiceServices` and `VoiceRules` (what `hive serve` builds when
  `[entrance.voice]` is on), `InMemoryAudioNectar`, and the door, the route and the socket reader.

Nothing in the Entrance stores a password, an invite code, a session token or a private key in
the clear, and no log line or trail event carries a code, a key, a token, a signature, a password,
a human's words or a clip's audio.

## How to test this

```bash
uv run --frozen pytest packages/hivemind/tests/unit/entrance \
    packages/hivemind/tests/contracts/test_entrance_store_contract.py \
    packages/hivemind/tests/contracts/test_entrance_grant_contract.py \
    packages/hivemind/tests/contracts/test_entrance_session_table_contract.py \
    packages/hivemind/tests/contracts/test_entrance_auth_tables_contract.py \
    packages/hivemind/tests/unit/cli/compose/test_entrance.py \
    packages/hivemind/tests/unit/cli/compose/test_entrance_refusals.py \
    packages/hivemind/tests/unit/cli/test_serve.py \
    packages/hivemind/tests/e2e/test_hive_serve.py \
    packages/hivemind/tests/e2e/test_voice_on_hive_serve.py
    packages/hivemind/tests/e2e/test_mutual_tls.py
```

`test_entrance.py` starts `serve_hive` in every mode: loopback, and `vpn`, `lan` and `tunnel`
behind a self-signed certificate on a DNS name. Where mutual TLS is on, a client certificate the
Hive's authority issued is served and a client without one is refused at the handshake.
`test_entrance_refusals.py` makes `serve_hive` itself refuse each exposure rule. The mutual-TLS
end-to-end test binds this machine's own private address.

The route, gate, stream and runtime tests run a real Entrance (`builders.entrance.serving`): uvicorn
on loopback ports over a real Queen whose Warden runs on the Hive Stand's Cell, push deliveries to a
recording fake push service, and a device client (`builders.entrance.landing`) that enrols (Ed25519
or passkey), logs in and signs exactly as a program or a browser does; `builders.entrance.views`
opens the live views as a device does. A rig's settings can shorten the first-frame deadline and
the stream backlog, and its seed writes what a restart would find before the Entrance starts. `tests/e2e/test_hive_serve.py` runs `hive serve`'s own composition over
the Hive's SQLite file with a scripted provider: the console approves a program, which submits a
goal the Hive finishes and reads the Queen's reply. After changing a route or a model, run
`uv run --frozen python scripts/write_landing_board.py` and commit `docs/entrance/openapi.json`;
the drift test fails until you do.
