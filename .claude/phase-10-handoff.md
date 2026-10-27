# Phase 10 handoff: every step ticked, two defect-fix rounds in flight

> Branch `claude/pensive-knuth-i11ia3`, pushed at `6e05b34`. Every push was verified first in an
> isolated worktree at the exact commit: the last full run passed 10,354 tests (3 skipped). ruff,
> mypy, import-linter (10 contracts) and the five check scripts were all clean, and the Landing
> Board document matched. Every roadmap box from 10.1 to 10.8 is ticked. The user then said:
> "stop everything, create a handoff doc for a fresh agent". Two subagents were stopped
> mid-round. Their work is saved as four patches in `.claude/phase-10-handoff/`, and none of it
> is merged yet. Start with those patches. This file covers:
>
> - the state of the phase;
> - what is in flight and how to resume it;
> - every defect still open;
> - the decisions taken;
> - how the work was run;
> - what this sandbox needs;
> - the final report the user is still owed.

## Since this handoff: merged into `main`

Phases 6, 7 and 10 were merged into `main` on 2026-09-25, through the integration branch
`claude/ecstatic-knuth-5pgnvt`. Nothing in "In flight" or "Still open" was applied by that
merge. What changed for whoever resumes:

- **Numbering.** Phase 10's ADR-0031 to ADR-0035 are now ADR-0039 to ADR-0043. Its Waggle
  minors 6, 7 and 8 are now 8, 9 and 10 (wire string "1.10"). Phase 6 kept 0031 to 0034 and
  1.6; phase 7 took 0035 to 0038 and 1.7. This file's own text already uses the new numbers.
  The four patches keep the old ones, because they must match their base.
- **Where to work.** Branch from `main`, not `claude/pensive-knuth-i11ia3`.
- **Applying the patches.** Apply them exactly as below, on a worktree at `6e05b34` (or
  `fac57e7`), and then port the result onto `main`. They were cut before the merge, and these
  paths have since moved:
  - `queen/dispatcher/ready.py` is now the `queen/dispatcher/ready/` package (`dispatch`,
    `assign`).
  - `queen/ticks/{chat,intake}.py` now live in `queen/ticks/human/`.
  - `cli/compose/hive.py` is now the `cli/compose/hive/` package (`build`, `run`, `goals`).
  - `supervision/capping/{audit,raises}.py` are now `supervision/capping/audit/{sampler,raises}.py`.
  - `manifest/schema/{security,guard}.py` are now `manifest/schema/security/{tiers,guard}.py`.
  - The task drafts moved out of `brood_chamber/task/model.py` into `brood_chamber/task/draft.py`.
  - `build_provider_registry` moved from `cli/compose/deps.py` to `cli/stores.py`.
- **Closed by the merge.** An in-Cell Warden now has phase 6's model-backed judge
  (`cli/in_cell/deps.py`, `_with_judge`), so a Virtual Cell's audit samples are judged, not
  recorded inconclusive. `tests/e2e/test_audit_raise_on_virtual_cell.py` asserts it. The Guard
  Bee item below about this is done.

**Later (2026-10-27).** Everything in "In flight" was ported onto branch `finish/roadmap` from
`main` at `21e43754`, and the patches were deleted; see "In flight" for each item's commit.

## Start here

1. **Read, in this order:**
   - `CLAUDE.md`
   - `.claude/codingrules.md`
   - `.claude/roadmap.md` phase 10, lines 1439-1755 (its exit criteria start at 1720)
   - `.claude/subagents.md`
   - ADR-0039 to ADR-0043 in `docs/adr/`

   These are normative, and the ADRs were amended this session. See "Decisions taken".
2. **Confirm the head is green.** Run `uv sync --frozen --all-groups`, then the gates (see "How
   the work was run").
3. ~~Resume the two in-flight rounds from the patches.~~ Done on `finish/roadmap` (see "In
   flight"); the patches are deleted.
4. Work through "Still open", then run the whole-system real run (see "Real runs"), then write the
   final report (last section).

## The ask and the ground rules

- **The user's instructions, in order:**
  1. The earlier handoff: "start a new branch and begin phase 10, test thoroughly and resolve
     all bugs/issues found".
  2. This session's opening instruction: "reference phase 10 handoff and complete phase 10".
  3. "continue".
  4. Last: "stop everything, create a handoff doc for a fresh agent".

  Every defect found is to be fixed, not just noted. CLAUDE.md prefers real end-to-end runs
  wherever a phase's exit criteria can be exercised by one.
- **Branch and pushing.** Develop and push only on `claude/pensive-knuth-i11ia3`, with
  `git push -u origin claude/pensive-knuth-i11ia3`. Retry network failures with backoff: 2 s,
  4 s, 8 s, 16 s. Do not open a pull request unless the user asks. CI runs only on `main` and on
  pull requests, so the isolated gate below is the only check.
- **Commits.** End them with the attribution lines your own harness gives you. Put no model
  identifier in any file, code comment or commit message.
- **Ticking.** Tick a roadmap box only in the commit that lands the step's last piece.
- **Hard constraints:**
  - There is no Hive provider API key. Never borrow the coding agent's own credentials for the
    Hive.
  - A denied action. Appending a scratch test CA to the Hive venv's certifi bundle and adding
    `/etc/hosts` entries (`push.hive.test`, `hooks.hive.test`) was denied as a TLS/auth weakening.
    - Do not pursue that outcome any other way: no trust-store changes, no hosts remapping, no
      `SSL_CERT_FILE` tricks for the Hive's push client, and no declaring the docker bridge a VPN
      CIDR to allow plaintext webhooks.
    - The real web-push and webhook delivery legs of exit criteria 3 and 4 need the user's
      decision.
    - Allowed: a client pinning the Hive's own CA (the CLI's `--ca-file`, or `verify=` in a
      script), and a `--add-host` local to a throwaway client container.
  - Permission laundering. Never perform, for a subagent, an action that was denied to it.
    - Example: a `git rebase` of an agent's branch was refused as destructive.
    - Integrating an agent's commits into this branch by cherry-pick rewrites nothing, and is the
      normal method.

## State at handoff

### Roadmap: every phase 10 box is ticked

- **10.1 to 10.4, and the Night Veil floors 10.3a to 10.3d:** from before this session.
- **10.5 Hive Entrance.** Two listeners; routes; streams; the human inbox and chat; OpenAPI.
  - The loopback-only route set is now asserted exactly.
  - `operator add` is a loopback-only route that answers 409 `hivemind.entrance.single_operator`.
  - The Observation Hive build is served on both listeners.
- **10.5a Remote exposure.**
  - Device certificates are issued at approval.
  - `mutual_tls` follows the mode: on for `lan` and `tunnel`, off for `vpn` unless set.
  - Every mode starts through `serve_hive` over TLS, and all 25 refusal rules are exercised
    there.
- **10.5b Push channel.**
  - The Alarm push and its withdrawal are tested.
  - The named test is `tests/e2e/test_push_withdrawal.py`.
- **10.5c:** from before this session.
- **10.5d Enrolment.**
  - The steward route is tested with its switch on.
  - `hive remote enrol` works from the invite link alone.
  - The note: the approve and revoke screens belong to 12.8a.
- **10.5e Login, sessions, step-up and the Reducer.**
  - The Guard Bee's door rules reduce a real Entrance.
  - The note: break-glass actions are 13.2a, 13.4 and 13.4a; no key-change route exists yet.
- **10.5f Voice.** In at the Landing Board.
  - A `keep_audio` clip is kept only in memory until the Nectar intake of 7.4 exists.
- **10.6 Guard Bee.**
  - Composed into every `hive run` and `hive serve`.
  - Integrity rules, attribution, and reading of Night Veil segments.
  - Note: C2 deposits are a seam until phase 7.
- **10.6a Isolation.**
  - Docker cuts egress on a per-Hive control network.
  - The in-Cell taint travels by Waggle 1.10's `cell.taint_order`.
  - QEMU declares why it cannot cut egress.
- **10.6b to 10.6d:** from before this session.
- **10.7 Access levels.** The note names the phase 11 and 12 seams.
- **10.8 CLI.**

### Exit criteria (roadmap line 1720)

**1. Drone without `net:*`; `prefer = "real"`; Warden without `forage:request`.**
- Automated proof: `tests/e2e/test_phase10_exit_criteria.py` (1a, 1b, 1c).
- Real run: 1b on a real Docker Cell (a goal lacking `cell:hive_stand` lands Virtual).
- Not provable here: nothing.

**2. Laptop `hive run --remote` and `hive inbox --remote`; refusals.**
- Automated proof: `test_phase10_remote_laptop.py` and `test_mutual_tls.py` (lan with
  certificates).
- Real run: a laptop container over the vpn listener. It enrolled, ran a goal, answered from its
  inbox, and was refused unauthenticated, pending, locked and revoked.
- Not provable here: nothing.

**3. Phone by QR, pending, passkey and password, question by web push, 404 on approve.**
- Automated proof: `test_phase10_phone.py`. It checks that the QR shown equals
  `segno.make_qr(link)`, and the Web Push is decrypted with the phone's key.
- Real run: a phone container on the vpn listener, with a software passkey, over TLS pinned to
  the Hive's CA. It enrolled, was pending (401), was approved at the Stand, logged in, spoke and
  confirmed a goal, got the question on the live push socket, answered by push-to-talk, and got
  a 404 on approve. Evidence is below.
- Not provable here:
  - delivery through a real push service (blocked by the denied TLS change);
  - a camera decoding the QR;
  - phone hardware;
  - the phone's screens, which are 12.8a.

**4. Program from the document, signed webhook, capability refusal, over-cap step-up.**
- Automated proof: `test_phase10_program_webhook.py` and `test_landing_board_conformance.py`.
- Real run: none.
- Not provable here: a webhook to a real receiver over TLS (the denied TLS change).

**5. Five bad passwords lock until a loopback unlock; reduce closes remote sessions; lan without
mTLS refuses.**
- Automated proof: e2e and unit tests, `tests/unit/cli/compose/test_entrance_refusals.py`, and
  `test_mutual_tls.py`.
- Real run: lockout and loopback unlock; reduce closed the live remote session (4411), then open;
  `lan` without mTLS refused to start.
- Not provable here: nothing.

**6. Spoken goal on local Whisper, echoed, confirmed, run; one TRANSCRIBER `llm.call`; no audio
bytes.**
- Automated proof: `test_voice_on_hive_serve.py`.
- Real run: in the phone run above, with a scripted OpenAI-compatible transcriber. There were two
  TRANSCRIBER calls, one per clip. The marker bytes were found in none of 29 files, and the
  words were in neither the trail nor the logs.
- Not provable here: local Whisper itself (huggingface.co is blocked).

**Phone run evidence** (script and evidence were scratch files, now gone), in order:
1. The invite link's fragment carried `code` only.
2. A passkey enrolled on the remote origin.
3. Login while pending was refused with 401.
4. The operator approved at the Stand with `hive entrance approve <id> --spend-cap 50 --interactive`.
5. Passkey plus password opened a session on the remote listener.
6. The push socket was opened with its `Origin`.
7. The spoken goal went to `AWAITING_CONFIRMATION` and was confirmed.
8. `question_waiting` arrived about the question id (a `msg_` id).
9. The push-to-talk answer came back `ANSWERED`, and the task went `RUNNING` with no
   confirmation step.
10. `withdrawn` arrived, then `goal_completed`, with the task SUCCEEDED.
11. A remote approve got 404.

## In flight: landed on `finish/roadmap` (2026-10-27)

Both rounds were ported by hand onto the current code (the paths had moved: the shared attach path
is `queen/attach/`, the bounded-loop runner now holds the Drone's loop) and the four patches were
deleted. One regression test per fix.

### Night Veil round 3

1. **Done** (`d547dbb2`). The SECURITY Alarm's `alarm.escalated` row carries `cell_id` as its own
   payload field, so the veil keeps it in the Cell's segment; `was_shown` reads through
   `query_cell`, so the human still sees the CRITICAL Alarm once while the Cell lives.
2. **Done** (`251f5d17`). `ChatLog.purge_night_veil`: a chat line naming the Cell or its ids is
   reduced to its skeleton in place (its position is kept, so no stream cursor moves).
3. **Done** (`251f5d17`). `GuardRequestStore.purge_night_veil` deletes the report, evidence and
   decision, but never a request still holding goals off a Cell. Both stores are `SideChannels`
   (`chat`, `guard_requests`); `HiveStores.guard_requests` is now the Queen's one store. The
   `alarm.escalated` exclusion in `tests/e2e/test_guard_bee_on_night_veil.py` is gone, and the
   e2e asserts the trail, the chat and the Guard requests after teardown.
4. **Done** (`fad1729c`). `queen/attach/serve.py` relays a snapshot or rollback only about the
   Cell the link proved; any other is answered with an error and recorded as
   `guard.relay_refused` (the `relay_forgery` dire pattern).
5. **Done** (`882cbcae`). `queen/inbox/claims.py` (`TaskClaims`) refuses a Warden's result,
   progress, Alarm, question or answer about a live task not placed with it, before `decide`, as
   `guard.task_refused` (the judged `task_claim` rule).

### Supervision round 4

- Item 1 (redial) was already on `main` as `cli/in_cell/redial/`.
- **Done** (`495bc712`). A clustered task the Queen cancels or fails leaves the clustered set;
  CLUSTERED -> ACTIVE -> WATCH (`settle_path`).
- **Done** (`441b332f`). Rebind and Takeover through `Warden.intervene` start the bee they promise
  (`SuccessorOrder`).
- **Done** (`424b8fbe`). Attempt numbers never go backwards: the Warden reports each new attempt
  on `TaskProgress(STARTED)`, the Queen keeps an `AttemptBook`, the table reads an `AttemptTally`.
- **Done** (`99c664b7`). A bounded-loop bee records its context every model round
  (`bounded_loop.runner._MeteredGate`); `tests/e2e/test_context_reset.py` proves both resets.
  A Compact and the bee's own threshold wait for an episode's first tool call
  (`LoopExecutor._should_hand_off`), so a fresh episode whose first round is already over the line
  cannot hand off forever with nothing done; Handoff, Rebind and Takeover still stop at once.
- **Done** (`51656aa2`). The Queen holds a Warden's liveness while her backend freezes its Cell
  (`attach.frozen_cell`).

## Still open after the patches (resolve; do not merely note)

Grouped by owner area. The first group belongs to phase 10 as found defects. The later groups
are named by the roadmap step that owns them.

**Phase 10, found this session, not yet fixed:**
- `queen/README.md` and the module docstrings touched by the patches: keep them true as you merge.
- `queen/ticks/alarms.py::_isolate`: the task claim (`882cbcae`) checks only the task an Alarm
  names; `AlarmRaised.context.cell_id` is never compared with the sending link's Cell. Under an
  operator ISOLATE policy row (none ships by default) a Warden could have the Queen isolate
  another Cell. Compare it with the proved link's Cell before acting.
- No manifest check ties `[memory] budget_fraction` (0.6) to the compact line (0.5) or the
  handoff threshold: a fresh episode can start over the compact line. The executor no longer
  loops on it, but a manifest warning when budget_fraction exceeds compact_at would say why.
- ~~A Cell release still runs inside the Queen's tick.~~ Fixed: every release runs beside the
  tick in `queen/dispatcher/releases.py` (a `ReleaseLane`, at most 4 at once, two of one Cell in
  order, collected each pass). Placement offers no task a Cell being released. `Queen.stop`
  awaits running releases before `retire_all`.
- ~~The Warden's own RETRY and REBIND skip the isolation resume gate.~~ Fixed: every Warden-side
  respawn (RETRY, REBIND, a Queen-ordered rebind, a context Handoff's successor) passes
  `admit_resume` in `wardens/ticks/alarms.py::_spawn_into_slot`. Tests in
  `tests/unit/wardens/isolation/test_gate.py`.
- ~~An isolated Docker Cell can still reach host services bound to `0.0.0.0` on the control
  gateway.~~ Resolved as documented (operator decision 2026-10-02: docs plus a warning, no
  firewall rule). `prepare_backend` probes each host-side `[llm.providers]` port on the control
  gateway once the network exists (`virtual_cell_providers.providers_answering_on`, one bounded
  TCP connect each) and logs `virtual_cells.provider_reachable_through_isolation` with the fix;
  `docs/guard/isolation.md` says how to bind a model server to docker0 instead.
- The Guard Bee:
  - ~~An in-Cell Warden has no model-backed judge.~~ Closed by the merge (see "Since this
    handoff").
  - ~~A handshake signature failure before attach is not recorded.~~ Fixed (`622b33de`): it is
    `guard.handshake_refused`, about the Hive, never the Cell the frame claimed.
  - ~~`control.error` is never sent in reply to a refused segment.~~ Fixed (`622b33de`): code
    `hivemind.queen.segment_refused.<reason>`, correlated to the chunk.
- Mutual TLS:
  - ~~There is no certificate renewal or re-issue route.~~ Fixed (`46589054`, `557ee9e8`, ADR-0058):
    `hive remote certificate renew` and `hive entrance reissue`.
  - ~~Expiry-driven revocation-list rebuild has no unit test.~~ Fixed (`a451e65a`): `sweep_once`
    over a real TLS handshake, in `tests/unit/entrance/runtime/test_tls.py`.
- ~~The four patches in "In flight".~~ Ported on `finish/roadmap` (see "In flight").

**Later phases, recorded in the roadmap's notes:**
- QEMU egress cut and QEMU Night Veil (design only, and fail-closed).
- The Nectar intake (7.4) for kept audio and C2 deposits.
- The Swarm's node storage and Pollen access (11.x).
- The web screens (12.x).
- The Android push channel (12.12).

## Decisions taken this session (do not relitigate without a new reason)

- **View read models** live in `observation/views/` (codingrules 8.11). The Entrance imports them
  only through the `hivemind.observation` face. import-linter ranks `entrance` over
  `observation`, and a forbidden contract keeps the edge to the face. ADR-0040 lists it.
- **A steward's approval** may grant no more spend per day and no later expiry than the
  steward's own (`enrol.grants.steward_terms`). ADR-0041 was amended to say so.
- **`operator add`** is a loopback-only route that always answers 409 while the Hive keeps one
  operator.
- **CLI help** renders as plain text (`rich_markup_mode=None`). A test fails if a `[section]`
  that a help text names is lost.
- **Test seams on `ServedHive`:** `push_transport` and `plan`. None means production. No manifest
  can set either.
- **Mutual TLS.** A client certificate is issued from a CSR in every remote mode. A passkey device
  gets a PKCS#12 bundle only under mTLS, and only from the loopback approve; it is never stored.
- **Docker egress cut.** Opt-in via `[virtual_cells] control_subnet`. Each Cell is dual-homed on
  the control network plus its own egress network, and the cut is `network disconnect`.
- **Night Veil Cells** are never offered to placement for any task. QEMU refuses NIGHT_VEIL.
- **Waggle 1.10** adds `GrantIssued.audit_raises` and `cell.taint_order` (both additive).
- **Guard Bee attribution.** Ownership is learned only from Hive Stand records. A fact about
  another Cell's subject becomes `guard_bee.subject_forged` against the Cell that recorded it
  (rule `subject_forgery`).
- **Dispatch:**
  - At most 4 provisions run in parallel, beside the tick.
  - A failing backend is rested from 1 s, doubling to a 300 s cap.
  - Placement failures and waits are recorded once per cause.
- **The Night Veil purge** deletes memory rows (episodes, Handoffs, Bee Bread, notes, wax). This
  is the one documented exception to append-only.

## How the work was run

**The loop.** The orchestrator dispatched a few large briefs to subagents, each in its own
worktree under `.claude/worktrees/<name>` on its own branch.
- Integration is by cherry-pick of each agent's range onto this branch.
- Conflicts were resolved by keeping both sides' intent.
- `docs/entrance/openapi.json` is regenerated with `scripts/write_landing_board.py`, never merged
  by hand.
- The Waggle spec's change-log entries are merged into one entry per version.
- Before every push, the full gate ran in a separate verify worktree at the exact commit, and the
  push happened only after `RC=0`.
- Keep the orchestrator's gate script in a directory of its own. An agent once overwrote a shared
  `scratchpad/gates.sh`, and that run proved nothing.

**The gate** (run it from the verify worktree):

```bash
#!/bin/bash
set -o pipefail
cd "$1" || exit 2
rc=0
uv sync --frozen --all-groups >/dev/null 2>&1 || { echo SYNC_FAIL; rc=1; }
uv run --frozen ruff format --check . 2>&1 | tail -3 || rc=1
uv run --frozen ruff check . 2>&1 | tail -15 || rc=1
uv run --frozen mypy 2>&1 | tail -15 || rc=1
uv run --frozen lint-imports 2>&1 | grep -E "Contracts:|BROKEN" || rc=1
for s in check_sizes check_fanout check_no_model_ids check_no_kind_branches check_no_transcripts; do
  uv run --frozen python scripts/$s.py 2>&1 | tail -8 || rc=1; done
uv run --frozen python scripts/write_landing_board.py --check 2>&1 | tail -5 || rc=1
uv run --frozen pytest -q -p no:cacheprovider -m "not integration and not live_llm and not local_llm" \
  packages scripts/tests > pytest-full.log 2>&1 || rc=1; tail -25 pytest-full.log
echo "== RC=$rc"; exit $rc
```

- The full suite takes 8 to 10 minutes. The end-to-end stretch, around 28 to 32 percent, is the
  slow part.
- Run from the main checkout, `scripts/check_sizes.py` also scans `.claude/worktrees/`, so read
  its findings with that in mind.

**Pitfalls seen this session:**

- **Cherry-picking a range:** one agent's range carried a package rename whose `__init__.py`
  landed later, so a few intermediate commits do not import. Pick whole ranges, and gate the
  head.
- **Real runs:** start the venv binaries (`.venv/bin/hive`), not `uv run`. The `uv` wrapper does
  not pass SIGINT on, and a stale `hive serve` then holds the ports.
- **A browser session's WebSocket** must send the listener's `Origin`, or it is refused with 4401
  (correct behaviour). The Python `websockets` client sends none unless given `origin=`.
- **The first `question_waiting`** a phone hears can be about its own held spoken goal (a
  `goalreq_` ref), not the Drone's question (a `msg_` ref).
- **Integration tests** need the Docker SDK extra:
  `uv sync --frozen --all-groups --all-packages --extra docker`.
  - Run them with `-m integration`.
  - `tests/integration/test_docker_egress.py` also needs `HIVEMIND_TEST_CELL_IMAGE=<tag>` of an
    image built from the same code as the Hive.
  - A Cell runs the Hive's own code, so an image built from another commit can disagree on
    Waggle 1.10.
- **CLI argument order:** group options come before subcommands, for example
  `hive inbox --remote --password-stdin answer ID TEXT`. `hive entrance revoke` has no `--yes`.

## Environment (this sandbox; a fresh container starts without most of it)

- **Blocked by the network policy:** `ollama.com`, `registry.ollama.ai` and `huggingface.co`, so
  no local model and no Whisper can be pulled. Also `astral.sh`, `ghcr.io` and GitHub releases.
  Docker Hub answers 429 (rate-limited). The user can change this in the cloud environment's
  Network access settings.
- **Docker.**
  - Start the daemon with a short exec-root, because the default socket path exceeds 104
    characters:

    ```bash
    dockerd --data-root <scratch>/docker/data --exec-root /run/hd --pidfile /run/hd/dockerd.pid &
    ```

    The CLI and the SDK then use `unix:///var/run/docker.sock`. Stop it at the end with
    `kill $(cat /run/hd/dockerd.pid)`, and leave no containers.
  - **Cell images.** This session built them on a local `base-ubuntu` image (Ubuntu 24.04 plus
    python3, from before Docker Hub started refusing):
    - a builder stage copies a local `uv` binary and the sandbox proxy CA;
    - it sets `SSL_CERT_FILE` in the builder stage only;
    - it runs `uv sync --frozen --no-dev --no-editable --package hivemind` into
      `/opt/hivemind/venv`;
    - the runtime stage copies only that venv, and runs as user `hive`;
    - built with `DOCKER_BUILDKIT=0 docker build -f Dockerfile.rebase -t <tag> .` from a
      `git archive` of the commit.

    In a fresh container, first try the repository's own `images/` Dockerfiles. Fall back to
    this recipe only if Docker Hub still refuses.
- **A private IPv4 address.** `test_mutual_tls.py` and the vpn and lan compose starts skip
  without one. docker0 (`172.17.0.1`) serves once dockerd runs. The real runs used `192.0.2.2`,
  added to eth0 with an `SIOCSIFADDR` ioctl because iproute2 is not installed. Client containers
  reached it through the bridge, with `--add-host hivestand.vpn.test:192.0.2.2`.
- **A scripted model server for real runs.** Serve
  `tests/e2e/scripted_openai.py::build_app(Scenario(...))` with uvicorn on a free port.
  - Bind the providers' `base_url` to it, and pin `[hive_stand.capacity] cores` so a busy host
    cannot zero the Drone's grant.
  - `Scenario.heard` markers are bytes. A JSON scenario needs a small runner that encodes them.

## Real runs done (CLAUDE.md: prefer a real task)

- **A real Docker Virtual Cell goal.** It found and fixed:
  - a false CELL_UNREACHABLE storm;
  - a Cell missing its `llm.call` rows;
  - zero grants under load;
  - Virtual Cells probing the host's load.
- **EC2:** a laptop container over the vpn listener.
- **EC5:** lan refusal, lockout and loopback unlock, reduce closing a live remote session, then
  reopen.
- **EC3 and EC6:** the phone container over the vpn listener, with passkey and voice.
- **Isolation on real Docker at `d1dfcd2`:**
  - Before the cut: DNS, the model server and the internet were reachable.
  - After the cut: all three failed with `OSError(101)`, while the Queen's link stayed open.
  - After the lift: all three were back.
  - The in-Cell taint was applied, and the resume was refused as `guard.scope.tainted_handoff`.
- **Dispatch on real Docker:**
  - A goal withdrawn mid-provision released its Cell (`task_gone`).
  - The tick handled Heartbeats during a 5.3 s provision.
  - A missing image produced 6+6 trail rows in 45 s, down from 90+90. The Cell was made once
    the image returned.
- **Supervision on real Docker:** an Overwintered Cell drew no false Alarm.
- **Night Veil teardown on real Docker:** snapshot images were removed, and containers ran with
  the `none` log driver.

**Still to do:** nothing from the whole-system run. It was briefed on 2026-10-02 and run on
2026-10-03, on branch `worktree-agent-ae04a13deb9e4009b` (from `fix/open-items` at `f51c5818`).
The host was Windows 11 with Docker Desktop 4.91 (WSL 2, engine 29.8).
- **Rig.**
  - The Cell image was built from this tree with `images/base-ubuntu`.
  - The scripted model server answered on `127.0.0.1`. Desktop's `host.docker.internal` reaches
    the host's loopback, so Cells reached it with no `0.0.0.0` bind.
  - The Stand's cores were pinned. No LM Studio and no GPU were used.
  - `hive serve` ran from the venv's `hive.exe`. Ctrl+C was delivered with
    `GenerateConsoleCtrlEvent` to a console of its own.
- **Windows Stand.**
  - Setup: `[placement] prefer = "virtual"`, the Docker backend, `egress_only`, and the vpn
    listener on the WSL vEthernet address `192.168.224.1`. TLS was for `hivestand.vpn.test`, from
    a scratch CA the laptop pinned with `--ca-file`.
  - A laptop container enrolled by invite and was approved on loopback, with matching
    fingerprints.
  - Its `hive run --remote` goal (no `cell:hive_stand`) was placed with `queen.placed`
    `ProvisionVirtual` on `docker`. The Drone asked, the laptop answered (`task.answered`), the
    task SUCCEEDED and the Cell was destroyed.
  - A second goal's Cell was isolated from the Stand's console while its Drone waited:
    - BLOCK wax, grant revoked, task PAUSED and question withdrawn;
    - the in-Cell taint order was applied (0 items: the bee was held in `ask` and had
      checkpointed nothing);
    - the CRITICAL SECURITY Alarm reached the laptop;
    - `hive cells lift` cleared the wax.
  - No `guard.handshake_refused` and no CELL_UNREACHABLE appeared.
- **Not possible on Windows: a `control_subnet`.** Docker Desktop keeps its networks in its own
  Linux VM, so the gateway is never a Windows address and the Queen's listener cannot bind it.
  The egress therefore stayed `unsupported`: the isolated Cell still reached the internet and
  the model server.
- **The closest honest variant: a Linux Stand inside Docker Desktop's VM.**
  - The same tree ran in a host-network container with the daemon's socket, with
    `control_subnet = "10.213.1.0/24"`, the vpn listener on docker0 (`172.17.0.1`) and a second
    laptop container.
  - The same flow passed, and the Cell was dual-homed.
  - After `hive cells isolate`, DNS, the internet, the model server (`host.docker.internal`) and
    docker0 all failed with `OSError(101)`. The Queen's link stayed open, with no redial and no
    CELL_UNREACHABLE.
  - After the lift, all of them answered again. So Desktop honours `internal`.
- **Defects found and fixed, each with tests and real-run proof:**
  - `c18395e9`: `hive serve` on Windows left the Cell running on Ctrl+C. It now owns SIGINT and
    SIGBREAK, and exits 0.
  - `9a8e99d8`: `run_hive`'s teardown was cut short in a cancelled task. This affected
    `hive run` + Ctrl+C on Windows and on Linux. The fix is `hivemind.common.tasks.run_to_end`.
  - `a42de970`: the isolation Alarm now says when the network is NOT cut.
  - `0b8c1d55`: a `control_subnet` whose gateway the host lacks is refused with a clear
    `ConfigurationError`. The docs had claimed Desktop worked.
  - `8d9b9449`: `hive trail tail -n` above 10000 printed a traceback; it is now a usage error.
- **Seen, not changed by the run.** Three of the four notes were closed afterwards on
  `worktree-agent-a3a961c6f2cc828bb`. Each has unit tests; the first also has an e2e
  (`tests/e2e/test_interrupted_run.py`), and the third a real `hive serve` stopped under a
  laptop's `hive run --remote`:
  - Closed (`282efbcc`, `25ff33dd`, `16605a00`, `99a768d2`): a goal whose `hive run` was
    interrupted stayed BLOCKED in the Brood Chamber, its question waiting while its Cell was gone.
    - `run_hive`'s teardown now ends with `hivemind.queen.requeue_unfinished`, once every Cell is
      gone. Each task still ASSIGNED, RUNNING or BLOCKED on a Warden this Queen placed work on
      goes back to PENDING as a new attempt, a BLOCKED one's question withdrawn first. The next
      start places it afresh and its bee asks again.
    - A task on a Night Veil Cell is cancelled instead, and a PAUSED one keeps its hold.
      `hive serve` shares the teardown.
    - PENDING, not a cancelled goal, because that is how the Hive already treats interrupted
      work: the stranded-ASSIGNED unassign, a goal request left PLANNING, Requeening's plan
      (13.1), and a follow whose timeout never ends the goal.
    - `hive run` now says, on a timeout or Ctrl+C, that the goal is not cancelled.
  - Tasks paused by an isolation stay PAUSED after the lift, by design. Still so.
  - Closed (`09b8a805`, `63b90d18`, `035854bc`): `honey_store.embed_failed` warned every 30 s
    against a server with no embeddings route.
    - The Ripener carries an `EmbedBackoff`. Retries double from 30 s to a 15-minute cap and
      start over on success.
    - The first failure is said in full, and a later one only on a new cause or at the cap.
    - A provider with no embeddings (404, 405, 501 or "not supported") is said once, naming the
      `[llm.slots]` binding to fix.
    - The openai_compat client now reads a 501 as a `ProviderRequestError`, which is how
      llama.cpp answers without `--embeddings`.
    - Proved on a real Hive over the scripted model server: one goal, then 100 s of 5-second
      ripening passes. The log held one `honey_store.embedder_unsupported` line naming
      `[llm.slots.embedder]`, and no `embed_failed` at all.
  - Closed (`145ec76f`): the laptop's follower called a serve shutdown "refused".
    - A follow the Hive leaves now ends with `HiveStoppedError`: a close with 1001 or 1012, a
      drop with no close frame, or nothing listening for a read.
    - `hive run --remote` says the Hive stopped and where the goal stood, then exits 3.
    - 4401, 4410 and every HTTP refusal are still refusals.
- **Seen while closing them, not changed:**
  - A PAUSED task bound to a Cell the stop retired keeps its binding. `resume_paused` needs its
    Warden attached, so after a restart such a task cannot resume until Requeening (13.1).
  - A crash, with no teardown, still leaves tasks on Cells that are gone. That is also
    Requeening's (13.1).
  - No local command cancels a goal. `ChatDoor.cancel_goal` is reached only through a device
    revocation, so an operator cannot drop an interrupted `hive run`'s goal from the CLI.
  - `hive run` takes no serve lock, so beside a running `hive serve` two Queens dispatch from one
    Brood Chamber. The new give-back touches only its own Queen's Wardens.
  - ~~The Night Veil purge cancels a BLOCKED task without withdrawing its question~~ **Fixed the
    same day, for every cancellation, not only the purge:** `_cancel` (chamber base) withdraws a
    still-ASKED question in the same store write as the cancellation (`task.cancelled` names it as
    `question_id`), and `_resolve_question` refuses (InvalidTransitionError) to resume any task
    not blocked on that very question, so a row from before the fix cannot be revived either.
    Device notices are still closed by Queen-level code (`human_channel.question_closed`); the
    purge has no such call, which matters only once Night Veil placement opens (phase 5 item 3).
  - A view still opening when `hive serve` stops finds nothing listening. The follower then
    says the connection dropped, not that the Entrance shut down, and exits 3 either way.
- **Cleanup.** No Cell, laptop or Stand container is left, and no hivemind network or volume of
  the run's. The image the run built was removed. No `hive` or model-server process remains.

## Defects found and fixed this session (for the final report)

**Entrance and CLI:**
- `hive serve` called a starting remote listener "reduced".
- ANSI colour codes went into log files, and log lines on stdout corrupted `--json`.
- Rich markup swallowed `[hive]`, `[entrance]` and `[llm.providers]` in 21 help texts.
- A steward could grant a higher daily cap, or a longer life, than its own.
- The loopback-only route test checked only a subset, and `operator add` existed only as a CLI
  refusal.
- `mutual_tls` defaulted on for vpn.
- The invite link could not enrol without `--hive`.
- The push socket crashed.
- Argon2id derivation deadlocked.
- A push-to-talk hold that went quiet closed the whole chat socket.
- The phone test's copied composition had drifted from `hive serve` (it lacked voice and the CA).

**Cell gate and Guard Bee:**
- The Cell listener never enforced the receiver rule, so a Virtual Cell could merge forged
  events under any node id, the Hive Stand's included.
- A Cell could frame another Cell's bee and get that Cell isolated.
- The Guard Bee could not see Night Veil Cells.
- A segment's `cell_id` was trusted without proof.
- The snapshot relay trusted `cell_id` (fixed in the in-flight patch).

**Night Veil:**
- Execution records outlived the Cell on the trail, in memory, in the Brood Chamber, in the
  ledger, in Docker snapshot images, and through the isolation's Cell Wax.
- A MEADOW task could land on a live Night Veil Cell.
- `hive cells destroy` recorded outside the veil.

**Queen and dispatch:**
- Provisioning blocked the tick, and `cell.provisioning` was stamped late.
- Cells were left behind by a cancelled or denied task.
- Placement read stale capacity.
- A finished task's grant was never released.
- Busy seats failed a task instead of making it wait.
- Placement-failure rows were written on every pass, and a failing backend was retried every pass.
- A provision failing its retry ended the Queen's run loop.

**Supervision:**
- A retry spawned beside the FAILED row.
- A context-size Handoff orphaned its task.
- A Cell paused on purpose drew a false Alarm.
- Clustering or isolation could crash a Warden's tick loop.

**Isolation:**
- A snapshot-rollback recreate dropped a dual-homed Cell's networks.

**Earlier in the session:**
- The handoff's known issues 1 to 7.
- The codingrules 8.11 placement of view models.

**The whole-system run's notes, closed afterwards (see "Real runs done"):**
- An interrupted or timed-out `hive run`, or a stopped `hive serve`, left its tasks BLOCKED,
  ASSIGNED or RUNNING on Cells that were gone.
- `honey_store.embed_failed` warned every 30 s, for as long as the Hive ran, against a provider
  with no embeddings route.
- The laptop's follower called a `hive serve` shutdown "refused".

## Final report owed to the user

When the work is done, report:
- what landed, step by step;
- every defect found and how it was fixed (the list above, plus whatever the patches and "Still
  open" add);
- what was proved end to end, and what could not be;
- explicitly: the network policy blocked `ollama.com`, `registry.ollama.ai` and `huggingface.co`,
  so no local model or Whisper could be pulled (also `astral.sh`, `ghcr.io` and GitHub releases,
  and Docker Hub answered 429). The user can change this in the cloud environment's Network
  access settings;
- the denied TLS trust change, and the real web-push and webhook legs of exit criteria 3 and 4
  that wait on the user's decision.
