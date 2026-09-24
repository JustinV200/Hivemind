# Phase 10 handoff: Guard Bees, Hive Entrance, auth and permissions

> Branch `claude/pensive-knuth-i11ia3`, cut from `main`. Each push was first verified green in an
> isolated worktree. The last code commit is `9c3623a`: 8020 passed,
> 2 skipped, and ruff, mypy, import-linter and the five hygiene scripts all clean. One piece is in
> flight: the Queen's half of 10.5. A subagent stopped at the session's usage limit with most of
> it written, and that work is saved as `.claude/phase-10-handoff/queen-human-path.patch`
> because it does not pass the gates yet. Start there. This file gives the state of every
> phase 10 step, what is left and in what order, the bugs still open, the decisions already
> taken, and how the work was run.

## Start here

1. Read `CLAUDE.md`, then `.claude/codingrules.md`, `.claude/roadmap.md` phase 10 (lines
   1439-1711), `.claude/subagents.md` and ADR-0031 to ADR-0035 in `docs/adr/`. CLAUDE.md's rules
   are non-negotiable, and the ADRs are the normative design for everything below.
2. Run `uv sync --frozen --all-groups`, then the gates (see "How the work was run") to confirm the
   head is green on your machine.
3. Apply the patch: `git apply --index .claude/phase-10-handoff/queen-human-path.patch`. It applies
   cleanly on `9c3623a` and on this handoff commit. Finish it as described under "The Queen human
   path", verify it, commit it, and push. Delete the patch file in the same commit.
4. Then work through "What comes next", in order.

## The ask and the ground rules

- The user's whole instruction was: "start a new branch and begin phase 10, test thoroughly and
  resolve all bugs/issues found". Phase 10 runs to its exit criteria, with real end-to-end runs
  wherever possible (CLAUDE.md), and every defect found gets fixed, not noted.
- Develop and push only on `claude/pensive-knuth-i11ia3`: `git push -u origin
  claude/pensive-knuth-i11ia3`, retrying network failures with backoff. Do not open a pull request
  unless the user asks for one.
- End commits with the attribution lines your own harness gives you. Put no model identifier in
  any file, code comment or commit body.
- CI (`.github/workflows/ci.yml`) runs only on pushes to `main` and on pull requests, so nothing
  checks this branch automatically. The isolated verification below is the gate.
- Tick a roadmap box only in the commit that lands the step's last piece, including its named
  tests. Several steps are half done: see the status table.

## What landed (`git log --oneline main..HEAD`)

- `b9ca8cb` The waggle IPv6 URI test skips on a host that cannot bind `::1` (this container
  cannot).
- `9083aec` Dependencies: FastAPI, uvicorn, webauthn and segno, each with a justification comment.
- `eeaed34`, `06a5973` The `[entrance]` manifest section (`manifest/schema/entrance.py`):
  - `bind` is loopback-validated.
  - `expose` is loopback, vpn, lan or tunnel; there is no public value.
  - `remote_bind` is never a wildcard.
  - Also: `public_url`, `tls`, `mutual_tls`, the session, step-up, lockout and rate-limit
    settings, `vpn_cidrs`, `vpn_interface`, `rp_id`, `tunnel_command`, `push` and `voice`.
  - Documented in `docs/manifests/full.toml`.
- `d7c6c7f`, `01bc8e6`, `f2f319d` ADR-0031 to ADR-0035, accepted after an adversarial review.
  Codingrules and the roadmap were aligned with them: section 4's subprocess allowlist names
  `hivemind.entrance.expose.tunnel`; section 8.15 covers exposure; Appendix C has rows for devices
  and the Entrance mode.
- `1a4f670` The guard core (10.1, 10.2, and the data half of 10.7):
  - The capability grammar: seven scope kinds; `net` is a HOST kind; two roots, operator and queen.
  - `evaluate`, a pure function with rule ids, whose floor hook is still empty.
  - `Enforcer`, which records `guard.denied`.
  - The EnforcementPoint catalogue, which classifies every trail kind.
  - `guard/access.py` and `guard/defaults/policy.toml`.
- `8902115` `common/secrets/` (a file store and a memory store) and one persisted Hive Ed25519
  signing key.
- `5a9203a`, `726552c`, `a0e70e3` Enrolment (10.4 and most of 10.5d):
  - The operator's password (Argon2id) and the console key wrapped under it.
  - Device keys (Ed25519 or passkey), the enrolled-device state machine and the Entrance tables.
  - Invites, redemption, approve and deny, lock, unlock, revoke and the expiry sweep.
  - Every status change writes its `guard.entrance_*` event in the same transaction.
- `ce2f724` Push (10.5b):
  - Webhooks are signed, destination-bound and SSRF-guarded, with the IP pinned and SNI kept.
  - RFC 8291 Web Push with VAPID; the payload is content-free, padded, with an HMAC `Topic`.
  - Also: the `LivePush` WebSocket hub, `PushDispatcher`, and its own migration series,
    `entrance_push`.
- `a7f5863` Enforcement points (10.3):
  - The goal set lives on `TaskSpec.capabilities`, and Waggle 1.6 carries
    `TaskAssign.capabilities` and `network_scopes`.
  - Checks: placement's goal ceiling, `forage:request`, tool authorisation, Warden spawn, rebind
    within a grant, question routing, Comb Shield egress, `cell:outside_scratch`.
  - `tests/e2e/test_phase10_exit_criteria.py` proves exit criterion 1 (tests 1a, 1b and 1c).
- `4381313` Fix: a goal whose set admits no Cell is cancelled once, instead of being re-refused
  on every tick.
- `4f77ba5` Remote exposure (10.5a), in `hivemind/entrance/expose/`:
  - `plan_exposure`, a pure check over gathered host facts.
  - The Hive's own certificate authority, with device certificates from a CSR or as PKCS#12.
  - A revocation list, and a TLS 1.3-only, ticketless mutual-TLS context rebuilt through
    `ContextSwitch`.
  - `TunnelSupervisor`, and the loopback Host check.
  - psutil is a new dependency.
- `9c3623a` Login, sessions, step-up and the Reducer (10.5e), under `hivemind/entrance/auth/`
  (`session/`, `login/`, `step_up/`, `confirm/`, `limits/`, `travel/`) plus `entrance/reducer.py`
  and migration 0002. Appendix C gained the pending-confirmation row.

## Roadmap status

| Step | State | What is left |
|---|---|---|
| 10.1, 10.2, 10.3, 10.4 | Done and ticked. | `ENTRANCE_ROUTE` stays in `PENDING_POINTS` (`guard/policy/catalogue.py`) until the app lands; `HONEY_ACCESS` stays there until phase 7. |
| 10.3a-10.3d, plus the Hive-state floor | Not started. | Everything; see `phase-10-handoff/brief-floors.md`. It reads the goal request id, so it goes after the Queen human path. |
| 10.5 (Queen half) | In the patch; failing gates. | See "The Queen human path". |
| 10.5 (Entrance app) | Not started. | See "What comes next", item 3. |
| 10.5a | Package landed. | Wiring into the app, CORS for `public_url` only, per-device rate limiting at the routes, and "a test starts the Entrance in every mode and asserts every refusal" on real listeners. The pure plan already asserts every refusal. |
| 10.5b | Channels landed. | The Queen's `HumanChannel` implemented over the dispatcher; re-validating subscriptions on start; the roadmap test "asks from the CLI, answers by webhook, asserts the web-push copy is withdrawn". The Android channel belongs to 12.12. |
| 10.5c | Not started. | `docs/entrance/landing-board.md`, the committed `docs/entrance/openapi.json` with a drift test, and a conformance client driven only by that document. |
| 10.5d | Flows landed. | Routes, the `hive entrance invite/approve/deny/devices/revoke/steward` CLI (10.8), and the "a device is asking to join" push. |
| 10.5e | Landed. | Two route-level tests (an approve on the remote listener is a 404; the Reducer closes a live WebSocket within a second) and the app wiring listed below. |
| 10.5f | Not started. | Voice at `POST /v1/chat/audio`, plus the minimal 6.5a subset it needs (the TRANSCRIBER slot). |
| 10.6, 10.6a-10.6d | Not started. | The Guard Bee (requests only), Queen-only isolation, the scanner, quarantine and taint. Waggle 1.7 is planned for `AlarmKind.SECURITY` and `InterventionAction.QUARANTINE`. |
| 10.7 | Data half landed with 10.1. | The rest of the step. |
| 10.8 | Not started. | The whole `hive entrance ...` CLI. |
| Exit criteria | Criterion 1 is proved. | The other five, end to end (see "Real end-to-end runs"). |

## The Queen human path (roadmap 10.5, Queen half)

The spec is `phase-10-handoff/brief-queen-human-path.md`, and the patch holds the work.

**Already written in the patch:**

- `queen/intake/`: `GoalRequest`, its states and transition table, a SQLite store (series
  `queen_intake`), a memory store, writes and budget.
- `queen/chat/`: the chat log (series `queen_chat`), posting, `HumanChannel` with a
  `NullHumanChannel` default on `QueenDeps.human_channel`, and the `ChatDoor` mixin. The mixin gives
  the Queen `request_goal`, `confirm_goal_request`, `decline_goal_request`, `post_human_message`
  and `acknowledge_alarm`.
- New ticks:
  - `queen/ticks/intake.py`: drains goal requests and recovers `PLANNING` rows through
    `TaskFilter(goal_request_id=...)`.
  - `queen/ticks/chat.py`: turns human messages into `HUMAN_MESSAGE` inbox items, carries out a
    REPLY, and marks messages handled.
  - `queen/ticks/awake.py`: fences the human's text as untrusted, and leaves the
    `scan_human_text` hook and a `TODO(10.6b)` for the scanner.
- REPLY:
  - `QueenAction.REPLY`.
  - `QueenDecision.message`, which is required on a REPLY and refused on any other action.
  - The autopilot and awake tables map REPLY.
  - The Queen system prompt and its snapshot are updated.
- `TaskSpec.goal_request_id` and `brood_chamber/task/goal_request.py`, persisted by the chamber's
  stores.
- `pheromone/events/families.py` was split into the package `families/` (codec, resources,
  supervisors, work), with the new `queen.goal_request_*`, `queen.human_message_received` and
  `queen.replied` kinds classified in `guard/policy/catalogue.py`.
- `queen.py` is 280 lines of code (the `Queen` class is 198, just under the 200-line class
  limit). Keep growth in delegate modules.

**Gate state with the patch applied (recorded on `9c3623a`).** lint-imports and the five hygiene
scripts pass. The rest:

- ruff, 4 findings:
  - ANN401 (`**overrides: Any`) in `tests/builders/human.py:59` and
    `tests/unit/queen/chat/test_model.py:33`.
  - E501 in `tests/contracts/test_goal_request_store_contract.py:71` and
    `tests/unit/queen/intake/test_writes.py:41`.
  - `ruff format` would reformat those two E501 files.
- mypy, 2 errors:
  - `tests/unit/queen/ticks/test_intake.py:70`: a `dict[str, str]` is passed where
    `dict[str, object]` is expected. Build it typed as `dict[str, object]`.
  - `tests/unit/queen/chat/test_channel.py:58`: `func-returns-value`, because the test uses the
    result of a method that returns None.
- pytest, 2 failures (8165 passed):
  - `tests/contracts/test_goal_request_store_contract.py::test_update_replaces_the_row_and_records_its_event`,
    both `[memory]` and `[sqlite]`.
  - `update` does record its event: `intake/memory.py` calls `trail.record` before the swap.
  - The test assumes the last element of `trail.query(TrailQuery())` is the newest. Check the
    query's documented order, and whether the two events tie on the FakeClock. Then fix the test
    by advancing the clock and reading in the documented order; do not reorder the store.

**Still to do** (the agent's last note was "add `prompt_text` to the human builders and write
the Queen-level test file"):

1. Fix the failures above.
2. Write the Queen-level tests from the brief's deliverable 10:
   - A request survives a simulated crash in each state and is planned exactly once.
   - Confirmation works.
   - A human message wakes the Queen with no Warden traffic, and reaches an awake episode whose
     assembled prompt contains it. Use `FakeLLMProvider` returning a REPLY decision, and check
     that a reply entry is produced.
   - Questions and Alarms appear in the chat.
   - The in-process `answer_question` path saves the keep-for-goal choice.
   - `HumanChannel` receives every call.
3. Add the goal-request state machine as a row in codingrules Appendix C.
4. Check that `hive run` and `hive tasks submit` still call `submit_goal` directly and behave
   exactly as before.
5. Run the full gates, verify in isolation, then commit and push. Delete the patch in that
   commit.

## What comes next, in order

1. **Finish the Queen human path** (above).
2. **The floors: 10.3a-10.3d and the Hive-state floor.**
   - `phase-10-handoff/brief-floors.md` is ready to hand to one subagent.
   - It also covers a phase 5 gap: the production composition root never builds
     `PlacementPolicy.night_veil`, so every Night Veil task fails placement today.
   - It also adds `hive run --comb-shield night_veil`.
   - It hardens `workers/tools/http.py` against loopback and the Hive Stand's own addresses by
     resolving, then pinning, with the name kept for SNI.
3. **The Entrance app** (the rest of 10.5):
   - `entrance/app.py`: two listeners from one route table, each a uvicorn server in the Hive's
     own loop.
   - The loopback listener carries approval routes; the remote listener never does.
   - `entrance/routes/` under `/v1/`, a `StreamHub` for the WebSocket streams, and the follower
     that obeys `guard.reduce_ordered`.
   - `landing_board.py`, the committed `docs/entrance/openapi.json` with a drift test, and the
     Queen's `HumanChannel` implemented over `PushDispatcher`.
   - `hive serve`.
   - Move `ENTRANCE_ROUTE` out of `PENDING_POINTS`.
   - Wire everything in the next section, and add the route-level tests 10.5a, 10.5b and 10.5e
     still owe.
4. **10.5c**: the Landing Board guide and the conformance client.
5. **10.8**: the CLI (`hive entrance invite|pending|approve|deny|devices|revoke|steward|reduce|
   open`, among others).
6. **10.5f**: voice, with the minimal 6.5a subset.
7. **The Guard Bee track**: 10.6, 10.6a, 10.6b (fill `scan_human_text`), then 10.6c and 10.6d
   together (Waggle 1.7).
8. **10.7**: the rest of access levels.
9. **The exit criteria, end to end**: fix everything the runs find, then tick the boxes.

## Wiring the Entrance app owes (collected from the landed steps)

- **Exposure:**
  - Call `gather_facts(section, SystemInterfaces(), clock, resolve_path=manifest.resolve_path)`,
    then `plan_exposure`, and start only what the plan names.
  - Build `server_context` inside a `ContextSwitch`; with uvicorn this works as `config.load();
    config.ssl = switch.listener_context`, as tested over real sockets.
  - Call `switch.rebuild` with a new `build_crl` on every revocation and on every start.
  - Build the tunnel's environment as `tunnel_environment(os.environ,
    overrides.entrance_tunnel_environ, withheld=<every provider's api_key_env>)`.
  - Stop `TunnelSupervisor` from `RemoteListenerControl.stop`.
- **Loopback listener:** middleware calling `loopback_request_allowed(host, header_names,
  bound_port)` on every request, answering a bare 403 on failure.
- **Sessions:**
  - The 5 s first-frame deadline (`SOCKET_HELLO_DEADLINE_S`).
  - Close a session's sockets when the session ends.
  - Offboarding runs `SessionBook.offboard` together with `PushDispatcher.forget_device`.
- **Rate limits:** per route, with the limiters from `auth/limits/`.
- **Relying parties:** one per listener. Loopback uses `localhost`; remote uses the plan's
  `rp_id`. Enrolment and login pick theirs by the arrival listener.
- **VAPID:** the subject defaults to `public_url` when `HIVEMIND_ENTRANCE_VAPID_SUBJECT` is unset.
- **Reducer:**
  - `hive entrance reduce|open`.
  - A Guard Bee rule that orders a reduction (`ReduceReason.GUARD_ORDER`).
  - The `RemoteListenerControl` and `StreamCloser` seams.

## Known issues and follow-ups (the user asked for every one resolved)

1. **Trail events.** No trail kind exists yet for a successful login, a session ending, or a
   pending confirmation being held, confirmed, cancelled or expired; today these are only logged.
   Add them to `families/` once the patch lands (the split made room), and classify them in the
   catalogue.
2. **Security notices.** The `SecurityNotifier` does not tell other devices about a reduction or a
   pending hold, because `SecurityNotice` is addressed to one device. It needs a broadcast form.
3. **Stuck dependents.** A task that depends on a failed or cancelled task stays `PENDING`, so
   `hive run` waits out its whole timeout. Reproduce it in an e2e run and fix it.
4. **Unescaped scratch paths.** `guard/access.py::fill_scratch` substitutes the scratch root into
   capability globs without escaping glob metacharacters, so a root containing `*`, `?` or `[`
   widens the grant. Escape them, and add a test.
5. **Flaky reports to confirm or clear:**
   - `tests/unit/queen/autopilot/test_effort.py`: seen once, while other agents were editing.
   - `tests/unit/entrance/expose/test_tunnel.py`: passed 12 concurrent runs at three times CPU
     oversubscription, so it is probably the same cause.
   - Codingrules: a failing test is never "just a flake".
6. **Exposure limits that are documented, not fixed:**
   - A public suffix used as `rp_id` (`ts.net`) is not detected; the browser refuses it at the
     first ceremony.
   - A bare TCP forwarder in front of the loopback listener is invisible at the HTTP level, so the
     client guide (10.5c) must forbid it.
   - On Windows, stopping the tunnel ends only the child, not processes it started.
7. **Stale docs.** `entrance/routes/` is still a skeleton. Update `entrance/README.md` when the app
   lands.

## Decisions already taken (do not relitigate without a new reason)

- **Exposure:**
  - Mutual-TLS contexts are TLS 1.3 only, with no session tickets, because TLS 1.2 session-id
    resumption and 1.3 tickets both let a revoked device back in. Without mutual TLS, the floor
    is 1.2.
  - `vpn` and `lan` never bind a globally routable address: the rules `VPN_BIND_GLOBAL` and
    `LAN_BIND_NOT_PRIVATE`. `lan` needs RFC 1918, link-local or an IPv6 ULA address, and a global
    IPv6 address or carrier-grade NAT space is refused.
  - `rp_id` must be `public_url`'s host or a parent domain of it.
  - The PKCS#12 bundle leaves out the CA certificate, since phones would install it as a
    trusted root.
  - The tunnel child's environment drops every `HIVEMIND_*` variable and any withheld provider-key
    names.
  - The loopback check refuses `Forwarded`, `X-Forwarded-*`, `X-Real-IP`, `Via` and
    `Tailscale-*`.
  - The CA lives in `expose/tls/`, not the ADR's `entrance/tls/`.
- **Authentication:**
  - The key proof comes before the password. An invalid proof is charged to the address; only a
    valid proof with a wrong password counts toward the device's lockout.
  - The travel lock is strict: a device's first remote login counts as a new network, and a peer
    the source cannot place is never trusted.
  - The console's sessions are kept in memory only.
  - `hold` refuses interactive devices and break-glass actions.
  - A pending confirmation lives 1 h by default.
- **Placement:** a goal whose set admits no Cell is cancelled once (`4381313`).
- **Push:** push keeps its own migration series (`entrance_push`).
- **ADRs:** ADRs are drafted as Proposed and accepted only after an adversarial review.

## How the work was run

**Gates.** Always run them with `set -o pipefail`: a `| tail` once hid a failing run.

```bash
set -o pipefail
uv run --frozen ruff format <only your own paths> && uv run --frozen ruff check .
uv run --frozen mypy
uv run --frozen lint-imports
for s in check_sizes check_fanout check_no_model_ids check_no_kind_branches check_no_transcripts; do
  uv run --frozen python scripts/$s.py; done
uv run --frozen pytest -q -p no:cacheprovider -m "not integration and not live_llm and not local_llm" packages scripts/tests
```

Limits worth checking before you commit:

- Source files stay under 300 lines of code and test files under 400 (`check_sizes`).
- Functions stay under 50 lines, with at most 5 parameters; classes under 200 lines.
- Split a test module by feature as `test_<module>_<feature>.py`, as `test_plan_refusals.py`
  does.

**Isolated verification before every push.** Keep a detached worktree, check out the exact
commit, and run every gate there, so uncommitted files cannot make a commit look green:

```bash
git worktree add --detach <scratch>/verify <sha>   # once; later: git -C <scratch>/verify checkout -q --detach <sha>
cd <scratch>/verify && uv sync --frozen --all-groups && <the gates above, with ruff format --check>
```

**Subagents.**

- Dispatch few, large, self-contained briefs, each owning disjoint paths. Tell each one:
  - which paths the others own;
  - to format only its own paths;
  - not to commit.
- The orchestrator (you):
  - reviews every security-relevant diff;
  - makes the small edits itself;
  - stages only that agent's paths (`git apply --cached` for a shared file's hunk);
  - commits and verifies in isolation;
  - then pushes.
- Subagent tokens count against the session's limit. This session hit it, and a subagent died
  mid-task, which is why the patch exists. Budget for that.

**Pitfalls seen:**

- `uv add` rewrites `pyproject.toml` comments, so restore the justification comments afterwards.
- A Monitor regex that matches "All checks passed!" fires on ruff, not on the end of the run.
- Tests run with `filterwarnings = error`. uvicorn needs `ws="websockets-sansio"` to avoid a
  deprecation warning.

## Real end-to-end runs (CLAUDE.md: prefer a real task)

**Constraints in this container:**

- There is no Hive provider API key. Never borrow the coding agent's own credentials for the Hive.
- The network policy blocks `ollama.com`, `registry.ollama.ai` and `huggingface.co`, so no local
  model can be downloaded.

**The plan:** a small scripted OpenAI-compatible server on loopback, configured as a provider with
a loopback base URL, returning scripted plans and decisions. It drives real runs of `hive serve`,
`hive run --remote`, enrolment, login, webhooks and Web Push against real sockets and real Cells.

**Already proved by smoke runs (not committed):**

- Two uvicorn servers run in one event loop.
- Mutual TLS through `ContextSwitch` refuses a revoked device on its next connection.
- Tunnel mode works end to end with a supervised forwarder.

The voice criterion needs a local Whisper model, which the network policy currently blocks.

## Final report to the user

When phase 10 is done, the report must cover:

- what landed, step by step;
- every defect found and how it was fixed;
- what was proved end to end and what could not be;
- an explicit note that the environment's network policy blocked `ollama.com`,
  `registry.ollama.ai` and `huggingface.co`, so no local model or Whisper could be pulled. The
  user can change this in the cloud environment's settings (Network access).
