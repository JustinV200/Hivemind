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
3. **Resume the two in-flight rounds** from the patches (see "In flight"):
   - finish each item;
   - gate the result and commit it;
   - push.

   In the same commit, delete the patches and the two stale briefs in `.claude/phase-10-handoff/`
   (`brief-floors.md`, `brief-queen-human-path.md`; both describe finished work).
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

## In flight (saved as patches; nothing here is merged)

### Night Veil round 3

Files: `nv3-committed.patch` (1 commit) and `nv3-wip.patch` (uncommitted work).

Both apply cleanly to `6e05b34`, in order:

```bash
git am .claude/phase-10-handoff/nv3-committed.patch
git apply .claude/phase-10-handoff/nv3-wip.patch
```

The brief, item by item:

1. **Not started.** The SECURITY Alarm about a Night Veil Cell: its `alarm.escalated` row names
   the Cell and outlives it on the durable trail.
   - Carry the Cell id as its own payload field so the veil routes the row.
   - `was_shown` should read through `hivemind.pheromone.query_cell`.
   - The human must still see the CRITICAL Alarm while the Cell lives.
2. **Not started.** `chat_entries` rows that name the Cell need a purge side channel at teardown.
3. **Not started.** `guard_requests` rows (report, evidence, decision) need a purge side channel
   too.
   - Then drop the `alarm.escalated` exclusion in `tests/e2e/test_guard_bee_on_night_veil.py`.
   - Then assert all three stores after teardown.
4. **Done in the committed patch** (SECURITY). The snapshot relay answers only about the Cell its
   link proved.
5. **Work in progress** (SECURITY). A `TaskResult`, or any Cell message naming a task, about a
   task not placed on the sending Cell is refused and recorded.
   - The work in progress adds `queen/inbox/claims.py`, `test_claims.py` and
     `test_queen_claims.py`.
   - The agent was writing "a Cell cannot finish, fail, block, alarm or answer for another
     Cell's task" when stopped. Finish it and run it.

### Supervision round 4

Files: `sup4-committed.patch` (5 commits) and `sup4-wip.patch`.

The agent cut these on `fac57e7`. Its first four commits apply to `6e05b34`. The fifth
(`fc0239f`) and the work in progress conflict with the Night Veil changes in
`queen/cell_gate/listener.py`, `tests/unit/queen/cell_gate/test_listener.py` and
`queen/attach.py`.

Apply them on their own base, then cherry-pick onto the head, keeping both sides' intent:

```bash
git worktree add -b sup4 <scratch>/sup4 fac57e7
cd <scratch>/sup4
git am <repo>/.claude/phase-10-handoff/sup4-committed.patch
git apply <repo>/.claude/phase-10-handoff/sup4-wip.patch
```

The brief, item by item:

- **Items 2 to 6, committed:**
  - A Drone reports its context every model round, so the Warden's context check can fire
    mid-attempt.
  - Rebind and Takeover through `Warden.intervene` start the bee they promise.
  - A cancelled or failed task leaves the Warden's clustered set.
  - Attempt numbers never go backwards on the trail.
  - The Queen holds a Warden's liveness while she freezes its Cell (a snapshot freeze).
- **Item 1, work in progress:** Docker Overwinter reuse past about 45 s.
  - The websocket keepalive drops a paused Cell's link about 47 s into the pause, and the
    in-Cell Warden never redials.
  - The fix under way:
    - `cli/in_cell/redial.py` (new);
    - `cli/in_cell/link.py` and `main.py`;
    - `queen/cell_gate/{gate,listener,provider}.py` accept the re-attach;
    - `queen/attach.py`.
  - Tests: `tests/unit/cli/in_cell/test_redial.py`, `tests/e2e/test_overwinter_redial.py`, and
    a hook in `tests/builders/virtual_cells.py`.
  - The agent was adding "a hook in the builder so a subclass can point its container at a
    relay" when stopped.
  - Finish it, and prove it on real Docker if you can: a Cell paused 60 s or more is reused for
    a second goal.

## Still open after the patches (resolve; do not merely note)

Grouped by owner area. The first group belongs to phase 10 as found defects. The later groups
are named by the roadmap step that owns them.

**Phase 10, found this session, not yet fixed:**
- `queen/README.md` and the module docstrings touched by the patches: keep them true as you merge.
- A Cell release still runs inside the Queen's tick: up to 5 s for the graceful stop plus the
  teardown. Provisioning moved beside the tick; release did not.
- The Warden's own RETRY and REBIND do not go through the isolation resume gate
  (`wardens/isolation/gate.py::admit_resume`). This is covered in practice, because grants are
  revoked on isolation and a Worker refuses a tainted Handoff itself. Make it explicit.
- An isolated Docker Cell can still reach host services bound to `0.0.0.0` on the control
  gateway. The doc tells operators to bind model servers to docker0. A real fix needs a host
  firewall rule, meaning root outside Docker's API, so this is the user's decision.
- The Guard Bee:
  - An in-Cell Warden has no model-backed judge, so its audit samples are recorded as
    inconclusive.
  - A handshake signature failure before attach is not recorded.
  - `control.error` is never sent in reply to a refused segment.
- Mutual TLS:
  - There is no certificate renewal or re-issue route. Certificates last 90 days, and a device
    approved while the Hive ran loopback only has none, so both mean enrolling again.
  - Expiry-driven revocation-list rebuild has no unit test.

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

**Still to do:** one whole-system run once the patches land.
- `hive serve` with a Docker Virtual side on a `control_subnet`, and a vpn remote listener.
- A laptop container's `hive run --remote` goal lacking `cell:hive_stand` lands on a Docker Cell,
  and its question is answered from the laptop.
- Then `hive cells isolate <id> --reason ...` and `hive cells lift <id>` from the Stand's
  console.
- Check the trail, and leave no containers.

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
