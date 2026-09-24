# Phase 6 handoff (Exoskeleton): state, map, open items

> For an agent starting with a clean context. Phase 6 was built on 2026-09-24 on branch
> `claude/sleepy-bardeen-vz4cz4`, in a Linux cloud sandbox. An orchestrator wrote the ADRs and the
> foundation itself and dispatched one subagent each for 6.5a, 6.11, 6.12, 6.5 and 6.6. Steps 6.1
> to 6.8, 6.5a, 6.11 and 6.12 are done and ticked. **Still open: a `file://` hole in the browser
> tools (security; fix it first), 6.9 Forager, 6.10 Scout, the phase's exit criteria end to end,
> and 6.13 Pheromone Mask, whose writing rules must come from the user before any code.**
> Section 4 lists them in order.

Read first:

- `CLAUDE.md` and `.claude/codingrules.md`: sections 3, 5, 7.2, 8.7, 8.12, 14, 15 and Appendix C.
- `.claude/roadmap.md`, phase 6 (it starts near line 1087).
- `.claude/subagents.md`.
- ADRs `docs/adr/0031-*`, `0032-*` and `0033-*`.
- `exoskeleton/README.md` and `workers/tools/exoskeleton/README.md`, which describe the layout as built.

Path shorthands used below:

| Shorthand | Path |
|---|---|
| `H/` | `packages/hivemind/src/hivemind/` |
| `W/` | `packages/waggle/src/waggle/` |
| `T/` | `packages/hivemind/tests/` |

## 1. Where things stand

**Branch.**
- `claude/sleepy-bardeen-vz4cz4` has 35 commits on top of `origin/main` at `67b3232`, all pushed. This document is the last of them.
- **There is no PR.** The user has not asked for one.
- `ci.yml` runs only on pushes to `main` and on pull requests, so GitHub CI has never run on this branch. `integration.yml` runs nightly or on manual dispatch.

**Roadmap and ADRs.**
- Ticked, each with a "Landed as" note where the layout differs from the plan: 6.1, 6.2, 6.3, 6.4, 6.5, 6.5a, 6.6, 6.7, 6.8, 6.11 and 6.12.
- Unticked: 6.9, 6.10 and 6.13.
- ADRs 0031, 0032 and 0033 are written. The third phase-6 ADR, `exoskeleton-scope-and-pheromone-mask-boundary.md` (next number 0034), lands with 6.13.

**Gates at handover (2026-09-24, whole repository). All green:**
- `ruff format --check` and `ruff check`.
- `mypy`: 1185 files.
- `lint-imports`: 10 contracts. Phase 6 added one that keeps Playwright inside `H/exoskeleton/browser/playwright/`.
- All five `scripts/check_*.py`.
- `pytest -m "not integration and not live_llm and not local_llm" packages scripts/tests`, with every extra installed: 7137 passed, 4 skipped, about 90 s.
- CI's unit job, reproduced in a fresh venv built by `uv sync --frozen --all-groups` with no extras (exactly as `ci.yml` does):
  - 7054 passed and 9 skipped; the Playwright and Whisper tests skip with a reason.
  - Coverage is 94.86%, and every floor in `scripts/check_coverage_floors.py` holds.
  - CI's e2e job in the same venv: 68 passed.
- Integration tests run for real in this sandbox:
  - `T/integration/test_exoskeleton_docker.py`: the desktop image equips a task inside a real container, in about 10 s.
  - `T/integration/test_rehearsal_real.py`: real Chromium.
  - The Docker backend and snapshot tests.
  - The real X11, PulseAudio and Chromium clauses of the contract suites.

**Not proven anywhere yet:**
- The Windows leg of `ci.yml`.
- The Windows and macOS browser locator paths.
- A live hosted-provider call with the larger tool set.
- A real Whisper model. The transcription checks used a loopback stand-in server and a stubbed model.
- The Night Veil image's Tor Browser stage. This sandbox cannot reach the Tor Project.

## 2. What phase 6 implemented (map)

| Step | Modules | Notes |
|---|---|---|
| Waggle 1.6 | `W/messages/capping/gui.py`, `W/messages/task/needs.py`, `W/messages/task/recon.py`, `docs/waggle/spec.md` | `ActionKind.GUI` carries typed `GuiStep`s: desktop pointer, keys and scroll; browser navigate, click, fill and press on an `ElementTarget`; `SAY`. Also added: `PostconditionKind.URL_MATCHES` and `REGION_CHANGED`, `RollbackMethod.GUI_STATE`, and `ElementTarget.from_subject`/`subject` (the ELEMENT_TEXT subject grammar). `TaskAssign` carries `exoskeleton`, the network scopes and `recon: tuple[ScoutReport, ...]`. New fields `TaskResult.scout_report` and `CellCapabilitiesReport.real_display_allowed`, and `AlarmKind.EXOSKELETON_FAILED`. Every change is additive with a default. |
| Foundation | `H/cell/local/background.py`, `H/cell/session.py`, `H/cell/needs.py`, `H/cell/local/probe.py`, `H/guard/capabilities.py`, `H/guard/access.py`, `[hive_stand] exoskeleton_real_display` | `CellSession.start/stop/is_running`: each process gets its own process group, and its pid is recorded with the lease. One `BackgroundTable` serves the Hive Stand and in-Cell sessions; `FakeSession` scripts it. `TaskNeeds.browser_only` and `audio`, with `exoskeleton_need()` and `from_wire`. The probe derives `can_start_display` and `has_audio` from the installed toolchain (openbox required) and `has_browser` from a Chromium. New capability family `exoskeleton:{display,real_display,audio,browser}`; `real_display` is in no ceiling. Lease scratch is now mode 0700. |
| 6.1 | `images/desktop-ubuntu/`, `images/night-veil-ubuntu/`, `images/base-ubuntu/`, `.github/workflows/integration.yml` | Nothing starts at boot. Night Veil now builds `FROM` desktop-ubuntu and fetches a pinned Tor Browser, verified with gpgv. base-ubuntu bootstraps uv from PyPI. integration.yml installs member extras (`--all-packages --all-extras`), the toolchain and Chromium, and builds desktop-ubuntu. |
| 6.2, 6.3, 6.8 | `H/exoskeleton/{compound_eye,antennae,buzz}/` (`base.py`, `x11.py`/`xdotool.py`/`pulseaudio.py`, `fake.py`), plus `x11.py`, `frames.py`, `geometry.py`, `commands.py`, `scratch.py` | ImageMagick `import` for frames and region digests, `xdotool` for input, PulseAudio `parec`/`paplay` for audio. Contract suites: `T/contracts/test_exoskeleton_contract.py`, `test_browser_contract.py`, `test_recording_store_contract.py`. |
| 6.4 | `H/exoskeleton/attach/` (`plan.py` is the pure plan; also `core.py`, `display.py`, `audio.py`, `ready.py`, `handle.py`), `H/wardens/spawn/equip.py` | A private Xvfb (`-displayfd`, no TCP, a fresh cookie) plus openbox, proven ready by a pointer round trip. A private PulseAudio with `norewinds=1`. The browser comes through an injected `BrowserLauncher`. HOME and the XDG directories point into scratch. `detach()` stops exactly what attach started, newest first. `spawn_sub_bee` equips and `stop_sub_bee` detaches; a Cell that cannot equip raises `EXOSKELETON_FAILED`. |
| 6.5a | `H/llm/transcription/`, `H/llm/providers/whisper/`, `H/llm/providers/openai_compat/transcription/`, `H/llm/fanner/transcription.py` | `ModelSlot.TRANSCRIBER` and `BoundTranscriber` chains. `Ears` (a gate plus a chain) travels `WardenDeps.ears` → `WorkerContext.ears`. `whisper_local` was added to the Virtual Cell and in-Cell provider-kind lists. |
| 6.5 | `H/workers/tools/exoskeleton/` (`offer.py`, `act.py`, `desktop.py`, `browser.py`, `look.py`, `audio.py`, `expect.py`, `arguments.py`, `errors.py`), `H/supervision/capping/gui.py`, `H/supervision/capping/gate/gui.py`, `GuiAllowlistCheck`, LLM media (`AudioPart`, `ToolResultPart.media`, `H/llm/providers/openai_compat/media.py`, the Anthropic mapping), `BoundModel.sees`/`.hears` in `H/llm/slots.py` | Action tools: click, move, type, press, scroll, browser_navigate/click/fill/press and say. Read-only tools: see and browser_screenshot (vision models only), browser_snapshot, browser_read and listen. Tiers come from `act.py` (see section 3). |
| 6.6 | `H/exoskeleton/surface/` (`core.py`, `steps.py`, `verify.py`, `evidence.py`), `H/exoskeleton/recorder/` (models, recorder, redact, store, sqlite with migrations, playback), `H/cli/recordings.py`, `H/cli/compose/exoskeleton.py`, `[exoskeleton]` in `H/manifest/schema/exoskeleton.py`, `H/supervision/capping/audit.py` (`review_applied`), `H/wardens/spawn/audited_gate.py`, `H/wardens/judge.py`, Bee Bread `RECORDING` | `ExoskeletonSurface` implements the gate's `GuiSurface`. The recorder keeps two tables in the Hive's SQLite file. `hive recordings list/show/export`; export writes `<id>.html` (frames inline, with a CSP that forbids scripts and network requests) and `<id>.json`. `JudgeRequest` gains `evidence` and `goal`. |
| 6.7 | `H/queen/planner/schema.py`, `H/llm/prompts/decompose_goal.md`, `H/wardens/acceptance.py`, `H/wardens/ticks/results.py`, `H/exoskeleton/rehearsal/` | Acceptance on URL_MATCHES and ELEMENT_TEXT is plannable only on Exoskeleton subtasks. The Warden checks it on the attached browser before it detaches. `export_procedure` → `rebase` → `rehearse` → `RehearsalReport`. |
| 6.11 | `H/exoskeleton/browser/` (`base.py`, `launch.py` with `ChromiumLauncher`, `locate.py`, `state.py`, `targets.py`, `keys.py`, `excerpts.py`), `browser/playwright/` (`connect.py`, `page.py`, `calls.py`, `storage.py`), `browser/fake/` (`browser.py`, `launcher.py`, `site.py`, `login.py`) | CDP listens on loopback, on a port Chromium picks. A checkpoint is the URL, the cookies and every origin's local storage. `login_site()` mirrors the static fixture site in `T/fixtures/sites/login/`. |
| 6.12 | `H/queen/placement/{decide,rules,inventory}.py`, `H/queen/dispatcher/{ready,snapshot}.py`, `[virtual_cells] exoskeleton_image` | Placement mirrors attach's plan, checked against the ceiling of the Cell's access level. The dispatcher now passes a Real Cell's access level; without it, every Real Cell used to fail closed at READ_ONLY. |
| Test support | `T/builders/{exoskeleton,gui,recordings,rehearsal,audio}.py`, `T/contracts/*_harness.py`, `T/fixtures/sites/login/` | `ScriptedSurface` (in `builders/gui.py`) is shared by the gate and Warden tests. `login_recording(site, suffix)` records the bee's login, including one rolled-back mistake. |

## 3. Decisions and behaviour worth knowing (beyond the ADRs)

- **Tiers of GUI actions** (`H/workers/tools/exoskeleton/act.py`):

  | Action | Tier |
  |---|---|
  | Input on a lease-started display; a page that stays on the Cell (`file://`, `about:blank`, a loopback host) | `scratch_write` |
  | Input on the operator's own display | `device_command` |
  | Anything that leaves the Cell | `network_egress`, plus `net:<host>` at the ALLOWLIST rung |

  `irreversible=true` raises any action to `irreversible`. The `file://` row is the hole in 4.1.
- **Alarms.**
  - A GUI action that rolls back raises `POSTCONDITION_FAILED` at once, not after three rollbacks.
  - A judge that REJECTs an applied irreversible GUI action makes the Worker's tool raise one CRITICAL `AUDIT_FAILED` through the Worker telemetry. That is the path the Warden's escalation policy sees (`PendingAlarm` carries its own reason and severity), and the default policy escalates to a person, which ends the attempt.
  - `review_applied` records the verdict and deposits the finding, but raises no second Alarm.
  - Sampled `audit_completed` still raises a WARNING `AUDIT_FAILED` on REJECT.
  - GUI Alarms name the recording: "GUI proposal <id> in recording <id>".
- **The judge.**
  - Frames reach it only when every binding in the JUDGE chain declares vision (`BoundModel.sees`); the same rule gates the `see` and `listen` offers.
  - Evidence text is capped at 30,000 characters and two frames, and never appears in a repr.
  - `goal` is the task's objective, capped at 4,000 characters. It is never the proposer's own reasoning.
- **Frames never become JSON.**
  - `Frame` refuses serialization.
  - The recorder stores PNGs in BLOB columns.
  - A secret is recorded as its length (`describe_step`) or as the mask (`redact_step`, which feeds procedure `SecretSlot`s).
  - Every other typed text, URL and snapshot is scrubbed for credential shapes.
- **Handoffs.** A Handoff's `do_not_redo` names every capped side effect, including `keep`, plus GUI actions the bee called irreversible. Other GUI steps are left out, because a resumed bee gets a fresh screen and browser.
- **Desktop and browser environment.**
  - The desktop requires openbox: on Xvfb 21.1, xdotool's pointer moves are silently ignored without a window manager.
  - xauth is not used; attach writes its own authority entry.
  - The Chromium sandbox is off inside a Cell or when running as root (`ExoskeletonConfig.browser_sandbox`). The Wardens get a `ChromiumLauncher` only when Playwright is installed.
- **Browser navigation.** A failed navigation waits up to 2 s for Chromium's error page to commit (`page.py`), and an overlapped navigation that ended on the error page is retried once. Before this fix, about one real-Chromium contract run in eight flaked.
- **CLI logging.** The CLI now configures logging once: to stderr, at WARNING, or at the level `HIVEMIND_LOG_LEVEL` names. Before, `hive run --json` broke on the first debug line.
- **Pre-existing bugs fixed on this branch.**
  - The Docker snapshot rollback's recreate spec dropped `extra_hosts` and `cap_add`, so a recreated Cell never dialled back. It failed at `main` too.
  - The IPv6 URI test now skips on hosts with no IPv6 loopback.
  - `hive llm slots` and `hive llm providers` crashed on a provider that only transcribes.

## 4. Open items, in suggested order

### 4.1 Security: `browser_navigate` can read any file the Cell user can read

**The hole.**
- `page_reach` (`H/workers/tools/exoskeleton/act.py:176`) tiers any local `file://` URL as `scratch_write`.
- That tier's checks are SCHEMA and SIZE_CAP only (`H/supervision/defaults/capping-tiers.toml`, `[tiers.scratch_write]`). The ALLOWLIST rung never runs.
- Even where it does run, `GuiAllowlistCheck` (`H/supervision/capping/checks/deterministic.py:158`) checks only `exoskeleton:browser` and `net:<host>`, never a path.

So a Worker can `browser_navigate` to `file:///home/<operator>/.ssh/id_rsa` and then `browser_read` it, bypassing the lease's path rules that the file tools enforce. On the Hive Stand, those are the operator's files. A `file://` page can also link or script its way to other `file://` pages.

**Fix (designed, not started), with three layers:**
1. **Tool.** Refuse, before proposing, a `file://` URL whose path is outside what the lease may reach: its scratch root, and read-allowed paths if you decide those count. Use the same refusal shape as the other `act.py` refusals.
2. **Peripheral, as defence in depth.**
   - `BrowserLaunch` (`H/exoskeleton/browser/base.py:198`) gains `file_roots: tuple[Path, ...]`, filled with the lease's scratch.
   - `navigate` refuses a `file://` URL outside the roots with `PeripheralError`, in both the Playwright and the fake browser.
   - The Playwright backend adds a `framenavigated` guard on the main frame. When a click or a script lands on a `file://` URL outside the roots, it resets to `about:blank` and fails the next read.
3. **Gate.** Consider tiering a `file://` URL outside scratch as `outside_scratch_write` (or refusing it outright), and give `GuiAllowlistCheck` a path check against the lease's allowed paths.

**Tests.** Cover the tool refusal, both browsers in `T/contracts/test_browser_contract.py`, and a click from a scratch page to an outside file.

**Also check.** The rehearsal fixture tests and `test_exoskeleton_docker.py` load pages from scratch; they must keep working. The static fixture site is served over loopback http in `test_rehearsal_real.py`.

### 4.2 Roadmap 6.9 Forager and 6.10 Scout (not started)

The subagent dispatched for this hit the usage limit before writing anything. The survey facts below were re-checked at HEAD on 2026-09-24.

**The roadmap asks for:**
- A Forager in `workers/roles/forager.py`: a bounded see/act loop that deposits page content as Nectar.
- A Scout in `workers/roles/scout.py`: recon on a strict budget, returning a `ScoutReport` the Queen reads before committing Foragers.
- Every existing role is a package, so build `workers/roles/forager/` and `workers/roles/scout/`.

**What exists:**

| Area | Facts |
|---|---|
| Waggle | `WorkerRole` (`W/messages/task/assignment.py:86`) already has FORAGER and SCOUT. `TaskAssign.role` is at :122 and `TaskAssign.recon` (at most 4) at :158; recon is never filled. `ScoutReport` is at `W/messages/task/recon.py:62`: `feasible`, a 1-1000 char `summary`, up to 16 findings, up to 16 suggested_steps, up to 8 risks, up to 8 targets. `TaskResult.scout_report` (`W/messages/task/reports.py:196`) is never copied along Worker → Warden → Queen. Waggle already carries everything, so no waggle change is needed. |
| Worker side | The `Worker` protocol is at `H/workers/base.py:95` (`run` at :108) and `WorkerOutcome` at :53. The Drone's `run` is `H/workers/roles/drone/role.py:86`: `build_registry(ctx)` → `_RecordingExecutor` → `run_tool_loop` inside `run_with_overflow_retry` → claimed or handoff outcome. `brief_for` (`drone/prompt.py:177`) does not render `assignment.recon`. Claimed results go through `_finish_claimed` (`H/workers/runtime/attempt.py:231`) → `ResultDetails` (`H/workers/runtime/reports.py:68`) → `build_result` (:145). No scout field exists anywhere on this path. |
| Warden | `_send_succeeded` (`H/wardens/ticks/results.py:94`) and `_send_acceptance_failed` (:114) rebuild `TaskResult` without `scout_report`. The Worker is chosen by `deps.worker_factory(assignment.role)` (`H/wardens/spawn/spawn.py:445`). Both composition roots define an identical `_build_drone(role) -> Drone()`: `H/cli/compose/deps.py:530` (wired :331; the file is near the 300-line limit) and `H/cli/in_cell/deps.py:172` (wired :127). |
| Queen | `_task_assign` (`H/queen/dispatcher/ready.py:403`) hard-codes `role=WorkerRole.DRONE` (:421) and passes no recon. DRONE is also hard-coded in `_grant_inputs` (:359, :365-366) and in the footprints in `H/queen/dispatcher/snapshot.py:95,135`. `QueenDeps.footprints` defaults to DRONE only (`H/queen/deps.py:377`). `[forage.roles]` keys map to `WorkerRole` in `H/cli/compose/deps.py:525`, and `REQUIRED_ROLE = "drone"` (`H/manifest/schema/forage.py:59`). No role field exists on `TaskSpec` (`H/brood_chamber/task/model.py:107`), `TaskDraft` (:253), `PlannedTask` (`H/queen/planner/schema.py:178`) or `TaskNeeds`. `complete_task` (`H/queen/ticks/results.py:58`) writes a `TaskOutcome` (`model.py:159`) that has no place for a report. |
| Nectar | `hivemind.honey_store` is an empty phase 7 skeleton. Until then Bee Bread is the Nectar body, the same stance ADR-0032 takes for recordings. `deposit_tool_result(result, task_id, clearance, ctx)` (`H/memory/bee_bread/deposit.py:110`) exists but nothing calls it. `_deposit` (:285) writes the entry and its trail event atomically. `BeeBreadEntryKind` is at `H/memory/bee_bread/entry.py:72`. Page reads come from `browser_read` and `browser_snapshot` (`H/workers/tools/exoskeleton/look.py`), already capped at 20,000 chars and password-redacted. `scrub_text` is at `H/exoskeleton/recorder/redact.py:58`. |
| Tools and planner | Exoskeleton tools are offered by `exoskeleton_specs(ctx)` whenever `ctx.exoskeleton` is set. `ACTION_TOOL_NAMES` and `READ_TOOL_NAMES` are exported from `H/workers/tools/exoskeleton`. `build_registry(ctx)` (`H/workers/tools/registry.py:212`) takes no role. |

**Design to implement.** Make documented choices where it leaves room, and keep changes additive.

1. **Role on the task.**
   - Add `role` (DRONE, FORAGER or SCOUT; default DRONE) to `PlannedTask` → `TaskDraft` → `TaskSpec`. Stored specs must still load as DRONE; check how the Brood Chamber stores `TaskSpec` and whether that needs a migration.
   - New planner rules, raised as ladder-retryable `ValueError`s like the existing ones: a FORAGER needs `needs.exoskeleton`, and a SCOUT's acceptance is FILE_EXISTS on `scout-report.json`.
   - Update `decompose_goal.md` to say when to plan each role:
     - a Scout before committing Foragers to an unfamiliar site or GUI, with a strict budget and the Foragers as its dependents;
     - a Forager for web or GUI work;
     - a Drone for everything else.
   - Regenerate the prompt snapshots (`T/unit/llm/prompts/snapshots/`, following its README).
2. **Dispatch.**
   - `_task_assign` uses the task's role. `recon` holds the `ScoutReport`s of its SUCCEEDED Scout dependencies: at most 4, newest first.
   - Footprints and grants: use the role's `[forage.roles]` entry when there is one, else the drone's. Do not make `forager` or `scout` required manifest keys.
   - Keep the Scout's report on the chamber `TaskOutcome`.
   - A `feasible=False` report fails the Scout task with a clear reason and is **not retried**, so the dependency machinery never dispatches its dependents. Find how failure and retry work in `H/queen/ticks/`; if no non-retryable failure exists, add the smallest documented one. An Appendix C edge may need adding.
3. **Result path.**
   - `WorkerOutcome.scout_report: ScoutReport | None = None`.
   - `ResultDetails` and `build_result` carry it onto the CLAIMED `TaskResult`.
   - `_send_succeeded` and `_send_acceptance_failed` copy it.
   - `complete_task` stores it.
4. **Worker selection.** One `worker_for(role) -> Worker` in `hivemind.workers.roles` maps DRONE, FORAGER and SCOUT to their Workers and raises a clear error for any other role. Both composition roots use it in place of `_build_drone`.
5. **Forager.**
   - A bounded see/act loop that reuses the Drone's machinery rather than copying it. Factor out a small profile (role, prompt name, round cap, tool selection, result hook) and leave the Drone's behaviour and tests unchanged.
   - Without an attached Exoskeleton it refuses: it returns a clear failed or handoff outcome and does not run.
   - Its brief renders the recon: each report's summary, targets, suggested steps and risks.
   - Every successful `browser_read` or `browser_snapshot` is deposited as a Bee Bread entry. Use `TOOL_RESULT`, or a new kind if that is clearer; a new kind means updating the closed-set guard in `T/unit/memory/bee_bread/test_entry.py`. The entry carries the task's clearance and names the URL; its text goes through `scrub_text` and is capped. Never deposit frames.
   - A new prompt `forager_system.md` (`PromptName.FORAGER_SYSTEM` and a snapshot) tells it to:
     - prefer structural reads and expectations over pixels;
     - declare `irreversible` for any step that cannot be undone;
     - never type a secret it was not given.
6. **Scout.**
   - A strict budget of about 6 rounds.
   - Read-only tools only: reads, `browser_navigate` and http GET are allowed. No fill, click, `run_command`, or write other than the report.
   - A `report_findings` tool whose schema is `ScoutReport`'s. It validates the report, writes `scout-report.json` through the normal capped write path so FILE_EXISTS can check it, and ends the loop with a claimed outcome carrying the report.
   - Running out of budget yields `feasible=False`, with a summary saying why.
   - A prompt `scout_system.md` (`PromptName.SCOUT_SYSTEM` and a snapshot).
7. **Tests.** Use the scripted `FakeLLMProvider`, the fake browser and `hivemind.exoskeleton.browser.fake.login_site`, and cover:
   - each role's happy path, plus the budget and refusal paths;
   - the Nectar deposits: content scrubbed, clearance kept;
   - the planner rules;
   - dispatch of role and recon;
   - the scout-report path from Worker to Warden to Queen;
   - an infeasible Scout holding its dependents back;
   - one flow through the real Queen, Warden and roles: a goal planned as Scout → Forager, where the Scout reports the login form's targets, the Forager's brief shows the recon, and it logs in to the fake site with its URL_MATCHES and ELEMENT_TEXT acceptance passing.

### 4.3 The phase 6 exit criteria, end to end (done, 2026-09-24)

`T/e2e/test_phase6_exit_criteria.py` (plus `T/e2e/phase6_exit_helpers.py`, the shared plan,
scripts and assertions) proves all three bullets below for real, on this sandbox: a Linux Real
Cell with a lease-started Xvfb, openbox and a real Chromium; a `desktop-ubuntu` Virtual Cell
(`ContainerSpawningFakeCellBackend`, an in-process in-Cell Warden on this host's own real
peripherals) with a deliberately wrong click rolled back and alarmed; the browser-only variant of
(a), which needs no X11 toolchain and so is the one placement that also runs unmodified on a
Windows Hive Stand -- `docs/runbooks/phase6-windows-exit-check.md` is what an operator runs there
to prove that leg for real. Three scenarios, ~20s here, no xfails.

A real Docker run (base-ubuntu then desktop-ubuntu rebuilt from this tree, `[placement] prefer =
"virtual"`, a scripted OpenAI-compatible stub server on the host reached through
`host.docker.internal`) proved the same login goal end to end against the real `openai_compat`
adapter and a real container: succeeded in 8.8s, one attempt, nothing left running.

Found and fixed along the way, in `hivemind.supervision.capping.audit` (`audit_completed`,
`review_applied`): both only caught `JudgeAnswerError` for "the judge could not answer", but
`WardenDeps.judge_reviewer` defaults to a bare `FakeJudgeReviewer()` with nothing scripted (no
Virtual Cell's Warden has a real `ModelJudgeReviewer` wired), whose own "queue is empty" signal is
a different exception, `JudgeUnavailableError` -- uncaught, it crashed the Drone exactly the way
the 2026-09-22 flake this module already guards against did, on a real Docker run's 10%
`network_egress` audit sample. Both call sites now catch both; two new unit tests
(`..._for_an_unscripted_reviewer`) cover it.

The roadmap's three bullets (near roadmap line 1188), for reference:

1. **The fixture login succeeds in three placements.**
   - A Linux Real Cell with a lease-started Xvfb can run here, on the Hive Stand.
   - The desktop-ubuntu Virtual Cell can run here too, with `nohup dockerd &` (section 5).
   - The Windows Hive Stand via Playwright cannot run here: it needs the user's Windows machine. Hand them a script or checklist.
2. **After each Real Cell run:**
   - no lease-started display, audio or browser process is alive;
   - the left-as-found snapshot holds, reusing the scenario (h) helpers from `T/e2e/test_kernel_on_hive_stand.py` and `T/e2e/kernel_helpers.py`;
   - no screenshot bytes appear in logs or on the trail. Grep captured logs and every trail payload for the PNG magic and for `iVBORw0KGgo`.
3. **Recording, playback and a wrong click.**
   - The login's recording plays back with before and after frames per action. The Observation Hive (12.4) does not exist yet, so use `hive recordings export` and check the HTML.
   - A deliberately wrong click fails its declared postcondition, is rolled back on the Virtual Cell, and raises an Alarm.

**Suggested shape:** `T/e2e/test_phase6_exit_criteria.py`, marked `e2e`.
- Skip with a reason when the toolchain, Playwright or Chromium is missing. CI's e2e job has no extras, so it will skip there; say so in the test.
- Serve the fixture site over loopback http from a temp directory. That keeps the tier `scratch_write` and works after the 4.1 fix.
- Script the planner reply (one Exoskeleton subtask with URL_MATCHES and ELEMENT_TEXT acceptance) and the Worker's tool calls: navigate, fill the username, fill the password marked secret, click submit with an `expect`.
- Run the Worker as the Drone today and the Forager after 4.2.
- For the Virtual Cell variant, follow phase 5's pattern: `ContainerSpawningFakeCellBackend` from `T/builders/virtual_cells.py`, with the in-Cell Warden run in-process on this host's peripherals.
- Also do one real Docker run with the desktop image (section 5), as phase 5 did in its handoff section 4a.
- CLAUDE.md prefers a real task. No model credentials exist in this sandbox, so a real-model run of the login goal is for the user's machine (LM Studio) or a later session with a key.

### 4.4 Roadmap 6.13 Pheromone Mask: ask the user first

The roadmap says: "More detailed writing instructions for pheromone mask ... ask me (user) for more info when you go to implement". **Ask before writing any code.**

What already exists:
- Two empty placeholder packages, `H/exoskeleton/tactics/` and `H/workers/tactics/`.
- The rules in codingrules: the layer table row near line 419, the supervisory-control rule near line 820, the UI badge rule near line 993, and the Appendix C mask row near line 1778.
- `H/supervision/mask.py` does not exist yet.

Also write ADR `exoskeleton-scope-and-pheromone-mask-boundary.md` (0034): what the Exoskeleton is for, what is out of scope (codingrules 15: not a stealth layer), and where the two tactics stop. The mouse tactic must keep replay metadata in the flight recorder.

### 4.5 Smaller items

1. **Night Veil teardown purge is not wired.** No composition root constructs `NightVeilTeardownPurge` (`H/pheromone/retention.py:178`). `night_veil_side_channels(recordings)` (`H/cli/compose/exoskeleton.py:286`) already returns the recorder's side channel for it. Night Veil placement still fails closed (phase 5 open item 3), so nothing leaks today. Wire both together.
2. **The real-time JUDGE check has no goal.** `JudgeCheck.run` (`H/supervision/capping/checks/judge.py:216`) builds its `JudgeRequest` without `goal`, because `CheckContext` has no task behind it. Carry the objective the way `AuditWiring.goal` does for the post-apply review.
3. **Live provider checks.**
   - The Anthropic mapping sends `strict: True` tools (`H/llm/providers/anthropic/mapping.py:414`), and the Worker's tool set now includes the Exoskeleton tools' nested `expect` schemas.
   - The OpenAI-compatible mapping sends images and audio in a user message after the tool messages.
   - Both are fixture-tested only. Run the `live_llm` suite with credentials, plus a real LM Studio run.
4. **DevTools port on shared Real Cells.** It is loopback-only, but on a multi-user Real Cell any local user can reach it. `--remote-debugging-pipe` would close this. Chromium's crash handler also runs outside the lease's process list. Both are recorded in ADR-0031's known limits.
5. **Windows and macOS.** The browser locator paths are unit-tested but have never run on those systems. Run the Windows leg of `ci.yml` by opening a PR when the user wants one.
6. **`integration.yml`** still apt-installs `xauth`. It is harmless but unused; the probe no longer requires it.
7. **Rebuild the images after any source change.** A container runs the package as built, not the checkout.

## 5. How to work in this sandbox

**Environment** (a fresh container needs all of this again):
- Full dev env: `uv sync --frozen --all-groups --all-packages --all-extras` (the Docker SDK, Playwright, faster-whisper).
- The toolchain was installed by hand: `apt-get install -y --no-install-recommends xdotool pulseaudio pulseaudio-utils openbox x11-utils imagemagick fonts-dejavu-core`. Xvfb was already present.
- Chromium is pre-installed under `PLAYWRIGHT_BROWSERS_PATH=/opt/pw-browsers` (`chromium-1194`). Never run `playwright install`.
- Docker: start the daemon by hand with `nohup dockerd > <scratchpad>/dockerd.log 2>&1 &`. It does not survive a container restart; `docker images` shows whether the dev images are still there.
- Always run `uv run python scripts/...`. The system `python3` is 3.11 and cannot parse the repo's PEP 695 syntax.

**Real-peripheral test notes:**
- The harnesses put scratch under `tempfile.mkdtemp(prefix="hm-", dir="/tmp")`, because PulseAudio's socket lives in scratch and a Unix socket path must fit in 108 bytes. pytest's `tmp_path` is too long.
- The sandbox runs as root, so Chromium runs without its sandbox here.

**Gates.** Run these from the repository root:

```bash
uv run ruff format --check . && uv run ruff check . && uv run mypy && uv run lint-imports
for s in check_sizes check_fanout check_no_kind_branches check_no_model_ids check_no_transcripts; do uv run python scripts/$s.py; done
uv run pytest -q -m "not integration and not live_llm and not local_llm" packages scripts/tests -p no:cacheprovider
uv run pytest -q -m integration packages/hivemind/tests/integration -p no:cacheprovider   # needs dockerd + images
```

To match CI's unit job exactly (no extras, plus coverage floors), use a separate venv:
`UV_PROJECT_ENVIRONMENT=<scratchpad>/venv-noextras uv sync --frozen --all-groups`, then `uv run --frozen pytest -m "not integration and not e2e and not live_llm and not local_llm" --cov --cov-report=json`, then `uv run --frozen python scripts/check_coverage_floors.py`. Delete `coverage.json` and `.coverage` afterwards; both are gitignored.

**Verifying a commit candidate while a subagent edits the tree.** Test it in a detached worktree with only the relevant files copied in, using the main venv and the worktree's sources:

```bash
SP=<scratchpad>; git worktree add -q --detach $SP/wt HEAD
cp <each changed file> $SP/wt/<same path>
cd $SP/wt && PYTHONPATH=$SP/wt/packages/hivemind/src:$SP/wt/packages/waggle/src:$SP/wt/packages/pollen/src \
  /home/user/Hivemind/.venv/bin/python -m pytest -q packages/hivemind/tests/unit packages/waggle/tests -p no:cacheprovider
```

**Building the images here.** HTTPS must go through the agent proxy and trust its CA, and the browser download hosts are blocked. This helper (a scratch file, never shipped) builds a copy of an image's Dockerfile:
- it injects the proxy CA after every `FROM`;
- it swaps `playwright install --with-deps chromium` for the pre-installed Chromium plus `playwright install-deps`.

Build base first, then desktop: `build_image.sh base-ubuntu hivemind/base-ubuntu:dev`, then `build_image.sh desktop-ubuntu hivemind/desktop-ubuntu:dev`.

```bash
#!/usr/bin/env bash
# Usage: build_image.sh <image-dir-name> <tag> [docker args]
set -euo pipefail
name="$1"; tag="$2"; shift 2
repo=/home/user/Hivemind
work=$(mktemp -d)
python3 - "$repo/images/$name/Dockerfile" "$work/Dockerfile" <<'PY'
import sys, re
src, dst = sys.argv[1], sys.argv[2]
lines = open(src).read().split("\n")
out, skip = [], 0
for line in lines:
    if skip:
        skip -= 1
        continue
    if "playwright install --with-deps chromium" in line:
        out.append("COPY --from=pw chromium-1194 /opt/playwright-browsers/chromium-1194")
        out.append("RUN /opt/hivemind/venv/bin/playwright install-deps chromium && chmod -R a+rX /opt/playwright-browsers && rm -rf /var/lib/apt/lists/*")
        skip = 2  # the two continuation lines of the original RUN
        continue
    out.append(line)
    if re.match(r"^FROM\s", line):
        out.append("COPY --from=ccr ca-bundle.crt /etc/ssl/certs/ccr-ca-bundle.crt")
        out.append("ENV SSL_CERT_FILE=/etc/ssl/certs/ccr-ca-bundle.crt CURL_CA_BUNDLE=/etc/ssl/certs/ccr-ca-bundle.crt REQUESTS_CA_BUNDLE=/etc/ssl/certs/ccr-ca-bundle.crt")
open(dst, "w").write("\n".join(out))
PY
proxy="${HTTPS_PROXY:-http://127.0.0.1:44835}"
docker buildx build --load --network host \
  --build-context ccr=/root/.ccr --build-context pw=/opt/pw-browsers \
  --build-arg HTTPS_PROXY="$proxy" --build-arg https_proxy="$proxy" \
  --build-arg NO_PROXY="$NO_PROXY" --build-arg no_proxy="$NO_PROXY" \
  -f "$work/Dockerfile" -t "$tag" "$@" "$repo"
rm -rf "$work"
```

**Budget.** Subagent tokens share the session limit (`.claude/subagents.md`). The Forager/Scout dispatch died on that limit. Prefer fewer, larger dispatches, and have the orchestrator run the gates and commit.

**Commits.** Commit messages end with the session's `Co-Authored-By` and `Claude-Session` trailers. Never name a model in a commit, PR or code.

## 6. Commits on the branch (oldest first)

| Commit | Summary |
|---|---|
| `784640c` | ADRs 0031-0033 |
| `32eba98` | browser and whisper extras |
| `026c998` | waggle 1.6 |
| `1a845d1` | CellSession background processes |
| `4df825b` | Exoskeleton needs, the real-display opt-in, probe |
| `4070907` | the exoskeleton capability family |
| `5fd020b` | desktop-ubuntu; Night Veil rebased with a verified Tor Browser |
| `e46ef60` | transcription provider (6.5a) |
| `48f962e` | scratch 0700 |
| `e9f1c71` | IPv6 test skip |
| `d8179e9` | peripherals and attach (6.2-6.4, 6.8) |
| `fad8ac6` | placement (6.12) |
| `edf76ef` | needs reach the Warden; equip each sub-bee |
| `f85615e` | GUI actions through Capping |
| `75c313f` | Ears on each sub-bee |
| `ffb250a` | judge irreversible GUI actions with evidence |
| `c5b0b84` | redacted typed steps |
| `f55042e` | Docker recreate spec fix |
| `08ed960` | structural acceptance (6.7) |
| `70aa0a2` | Playwright fast path (6.11) |
| `cf914c8` | Exoskeleton tools (6.5) |
| `9304857` | SQLite recordings, playback, both Wardens wired (6.6) |
| `e7a3ab0` | navigation race fix |
| `9a47249` | procedures and rehearsal (6.7) |
| `b4a9ad3` | a REJECT ends the attempt |
| `0919e16` | honest Handoffs, prompted media, whole-chain media offers |
| `801d464` | `prune_before` on the protocol |
| `6b0b091` | CLI logs to stderr |
| `05c79bd` | docs |
| `ba5f4df` | judge against the task's goal |
| `0f04fa8` | tick 6.5, 6.6, 6.8, 6.11 |
| `e635ab2` | real-Chromium rehearsal; tick 6.7 |
| `54797ed` | desktop image proven in a container; tick 6.1 |
| `032558a` | size limits |

This handoff is the 35th commit.
