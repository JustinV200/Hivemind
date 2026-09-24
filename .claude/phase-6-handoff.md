# Phase 6 handoff (Exoskeleton): state, map, open items

> For an agent starting with a clean context. Phase 6 was built on 2026-09-24 on branch
> `claude/sleepy-bardeen-vz4cz4`, in a Linux cloud sandbox, over two sessions. Steps 6.1 to 6.12,
> 6.5a included, are done and ticked, and the phase's exit criteria hold on real peripherals and
> in a real Docker container. The one leg this sandbox cannot run is the Windows Hive Stand; the
> operator runs `docs/runbooks/phase6-windows-exit-check.md` for it. **6.13 Pheromone Mask was not
> built; section 4.1 says why and what to do instead.** Section 4 lists everything left, in order.

Read first:

- `CLAUDE.md` and `.claude/codingrules.md`: sections 3, 5, 7.2, 8.7, 8.12, 14, 15 and Appendix C.
- `.claude/roadmap.md`, phase 6 (it starts near line 1087).
- `.claude/subagents.md`.
- ADRs `docs/adr/0031-*` to `0034-*`.
- `exoskeleton/README.md`, `workers/tools/exoskeleton/README.md` and `workers/roles/README.md`,
  which describe the layout as built.

Path shorthands used below:

| Shorthand | Path |
|---|---|
| `H/` | `packages/hivemind/src/hivemind/` |
| `W/` | `packages/waggle/src/waggle/` |
| `T/` | `packages/hivemind/tests/` |

## 1. Where things stand

**Branch.**
- `claude/sleepy-bardeen-vz4cz4` has 57 commits on top of `origin/main` at `67b3232`, all pushed. This document is the last of them.
- **There is no PR.** The user has not asked for one.
- `ci.yml` runs only on pushes to `main` and on pull requests, so GitHub CI has never run on this branch. `integration.yml` runs nightly or on manual dispatch.

**Roadmap and ADRs.**
- Ticked, each with a "Landed as" note where the layout differs from the plan: 6.1 to 6.12, 6.5a included.
- The exit criteria carry a "Met 2026-09-24" note. 6.13 is unticked, with a pointer to section 4.1 here.
- ADRs 0031, 0032, 0033 and 0034 are written. 0034 (a lease's browser reads only its own scratch) is the fix for the old `file://` hole. The roadmap's third ADR, `exoskeleton-scope-and-pheromone-mask-boundary.md`, belongs to 6.13 and is unwritten.

**Gates at handover (2026-09-24, whole repository). All green:**
- `ruff format --check` and `ruff check`.
- `mypy`: 1221 files.
- `lint-imports`: 10 contracts.
- All five `scripts/check_*.py`.
- `pytest -m "not integration and not live_llm and not local_llm" packages scripts/tests`, every extra installed: 7309 passed, 4 skipped, about 90 s.
- CI's unit job, reproduced in a venv built by `uv sync --frozen --all-groups` with no extras (exactly as `ci.yml` does):
  - 7218 passed and 10 skipped; the Playwright and Whisper tests skip with a reason.
  - Coverage is 94.8%, and every floor in `scripts/check_coverage_floors.py` holds.
  - CI's e2e job in the same venv: 70 passed, 3 skipped (the three phase 6 exit scenarios skip: no Playwright).
- `pytest -m integration` against images rebuilt from this tree: 6 passed, 1 skipped (QEMU is not installed here).
- Every e2e test also passes with every audit sample forced on (section 5 explains how).

**Real runs, not just tests:**
- `T/e2e/test_phase6_exit_criteria.py` drives a real Xvfb, openbox, xdotool and Chromium.
- A real `desktop-ubuntu` container, built from the final tree, ran the fixture login end to end in 10.8 s. The container's own Warden ran the Forager against the real `openai_compat` adapter and a scripted stub server on the host; Forage granted 2 sub-bees, four capped actions were applied and verified, and no container was left behind. An earlier run of the same login (at `72759f6`) also judged a sampled audit (APPROVE). The final run needed the host's page cache dropped first; section 4.3 explains why.

**Not proven anywhere yet:**
- The Windows leg of `ci.yml`, and the Windows exit check (the runbook).
- The Windows and macOS browser locator paths.
- A real model driving any of this. No credentials exist in this sandbox, so every model was scripted; a live hosted-provider call with the larger tool set has never run.
- A real Whisper model. The transcription checks used a loopback stand-in server and a stubbed model.
- The Night Veil image's Tor Browser stage. This sandbox cannot reach the Tor Project.

## 2. What phase 6 implemented (map)

| Step | Modules | Notes |
|---|---|---|
| Waggle 1.6 | `W/messages/capping/gui.py`, `W/messages/task/needs.py`, `W/messages/task/recon.py`, `docs/waggle/spec.md` | `ActionKind.GUI` carries typed `GuiStep`s: desktop pointer, keys and scroll; browser navigate, click, fill and press on an `ElementTarget`; `SAY`. Also added: `PostconditionKind.URL_MATCHES` and `REGION_CHANGED`, `RollbackMethod.GUI_STATE`, and `ElementTarget.from_subject`/`subject` (the ELEMENT_TEXT subject grammar). `TaskAssign` carries `exoskeleton`, the network scopes and `recon: tuple[ScoutReport, ...]`. New: `TaskResult.scout_report`, `CellCapabilitiesReport.real_display_allowed`, `AlarmKind.EXOSKELETON_FAILED` and `SCOUT_REPORT_FILE`. Every change is additive with a default. |
| Foundation | `H/cell/local/background.py`, `H/cell/session.py`, `H/cell/needs.py`, `H/cell/local/probe.py`, `H/guard/capabilities.py`, `H/guard/access.py`, `[hive_stand] exoskeleton_real_display` | `CellSession.start/stop/is_running`: each process gets its own process group, and its pid is recorded with the lease. One `BackgroundTable` serves the Hive Stand and in-Cell sessions; `FakeSession` scripts it. `TaskNeeds.browser_only` and `audio`, with `exoskeleton_need()` and `from_wire`. The probe derives `can_start_display` and `has_audio` from the installed toolchain (openbox required) and `has_browser` from a Chromium. New capability family `exoskeleton:{display,real_display,audio,browser}`; `real_display` is in no ceiling. Lease scratch is mode 0700. |
| 6.1 | `images/desktop-ubuntu/`, `images/night-veil-ubuntu/`, `images/base-ubuntu/`, `.github/workflows/integration.yml` | Nothing starts at boot. Night Veil builds `FROM` desktop-ubuntu and fetches a pinned Tor Browser, verified with gpgv. base-ubuntu bootstraps uv from PyPI. integration.yml installs member extras (`--all-packages --all-extras`), the toolchain (no xauth) and Chromium, and builds desktop-ubuntu. |
| 6.2, 6.3, 6.8 | `H/exoskeleton/{compound_eye,antennae,buzz}/` (`base.py`, `x11.py`/`xdotool.py`/`pulseaudio.py`, `fake.py`), plus `x11.py`, `frames.py`, `geometry.py`, `commands.py`, `scratch.py` | ImageMagick `import` for frames and region digests, `xdotool` for input, PulseAudio `parec`/`paplay` for audio. A pointer move to where the pointer already is skips `--sync`, which would otherwise wait forever. Contract suites: `T/contracts/test_exoskeleton_contract.py`, `test_browser_contract.py`, `test_recording_store_contract.py`. |
| 6.4 | `H/exoskeleton/attach/` (`plan.py` is the pure plan; also `core.py`, `display.py`, `openbox.py`, `audio.py`, `ready.py`, `handle.py`), `H/wardens/spawn/equip.py` | A private Xvfb (`-displayfd`, no TCP, a fresh cookie) plus openbox under a generated rc, proven ready by a pointer round trip. A private PulseAudio with `norewinds=1`. The browser comes through an injected `BrowserLauncher` with the lease's scratch as its only file root. HOME and the XDG directories point into scratch. `detach()` stops exactly what attach started, newest first. `spawn_sub_bee` equips and `stop_sub_bee` detaches; a Cell that cannot equip raises `EXOSKELETON_FAILED`. |
| 6.5a | `H/llm/transcription/`, `H/llm/providers/whisper/`, `H/llm/providers/openai_compat/transcription/`, `H/llm/fanner/transcription.py` | `ModelSlot.TRANSCRIBER` and `BoundTranscriber` chains. `Ears` (a gate plus a chain) travels `WardenDeps.ears` → `WorkerContext.ears`. `whisper_local` is in the Virtual Cell and in-Cell provider-kind lists. |
| 6.5 | `H/workers/tools/exoskeleton/` (`offer.py`, `act.py`, `desktop.py`, `browser.py`, `look.py`, `audio.py`, `expect.py`, `arguments.py`, `errors.py`), `H/supervision/capping/gui.py`, `H/supervision/capping/gate/gui.py`, `GuiAllowlistCheck`, LLM media (`AudioPart`, `ToolResultPart.media`, `H/llm/providers/openai_compat/media.py`, the Anthropic mapping), `BoundModel.sees`/`.hears` in `H/llm/slots.py` | Action tools: click, move, type, press, scroll, browser_navigate/click/fill/press and say. Read-only tools: see and browser_screenshot (vision models only), browser_snapshot, browser_read and listen. Tiers come from `act.py` (section 3). |
| 6.6 | `H/exoskeleton/surface/` (`core.py`, `steps.py`, `verify.py`, `evidence.py`), `H/exoskeleton/recorder/` (models, recorder, redact, store, sqlite with migrations, playback), `H/cli/recordings.py`, `H/cli/compose/exoskeleton.py`, `[exoskeleton]` in `H/manifest/schema/exoskeleton.py`, `H/supervision/capping/audit.py` (`review_applied`), `H/wardens/spawn/audited_gate.py`, `H/wardens/judge.py`, Bee Bread `RECORDING` | `ExoskeletonSurface` implements the gate's `GuiSurface`. The recorder keeps two tables in the Hive's SQLite file. `hive recordings list/show/export`; export writes `<id>.html` (frames inline, with a CSP that forbids scripts and network requests) and `<id>.json`. `JudgeRequest` gains `evidence` and `goal`. |
| 6.7 | `H/queen/planner/schema.py`, `H/llm/prompts/decompose_goal.md`, `H/wardens/acceptance.py`, `H/wardens/ticks/results.py`, `H/exoskeleton/rehearsal/` | Acceptance on URL_MATCHES and ELEMENT_TEXT is plannable only on Exoskeleton subtasks. The Warden checks it on the attached browser before it detaches. `export_procedure` → `rebase` → `rehearse` → `RehearsalReport`. |
| 6.9, 6.10 (Worker side) | `H/workers/roles/bounded_loop/` (`profile.py`, `runner.py`, `executor.py`, `prompt.py`, `outcome.py`, `records.py`, `sources.py`, `fields.py`), `H/workers/roles/forager/` (`role.py`, `nectar.py`), `H/workers/roles/scout/` (`role.py`, `tools.py`), `H/workers/roles/selection.py`, `H/llm/prompts/{forager,scout}_system.md` | The Drone's loop became `run_bounded_loop(ctx, assignment, resume_from, profile)`; Drone, Forager and Scout differ only in their `RoleProfile`. `worker_for(role)` maps DRONE, FORAGER and SCOUT through one registry; both composition roots use it. `WorkerOutcome.scout_report` rides the claimed result to the Warden and the Queen. |
| 6.9, 6.10 (Queen side) | `H/brood_chamber/task/model.py`, `H/queen/planner/schema.py`, `H/llm/prompts/decompose_goal.md`, `H/queen/dispatcher/{ready,snapshot}.py`, `H/queen/autopilot/table.py`, `H/queen/ticks/results.py` | `role` on `PlannedTask` → `TaskDraft` → `TaskSpec` (default DRONE; stored specs load as DRONE). `TaskOutcome.scout_report`. The dispatcher sends the task's role and its recon. An infeasible Scout fails without retry and cancels the work behind it. |
| 6.11 | `H/exoskeleton/browser/` (`base.py`, `launch.py` with `ChromiumLauncher`, `locate.py`, `state.py`, `targets.py`, `keys.py`, `excerpts.py`, `files.py`), `browser/playwright/` (`connect.py`, `page.py`, `calls.py`, `storage.py`, `guard.py`), `browser/fake/` (`browser.py`, `launcher.py`, `site.py`, `login.py`) | CDP listens on loopback, on a port Chromium picks. A checkpoint is the URL, the cookies and every origin's local storage. `login_site()` mirrors the static fixture site in `T/fixtures/sites/login/`. |
| 6.12 | `H/queen/placement/{decide,rules,inventory}.py`, `H/queen/dispatcher/{ready,snapshot}.py`, `[virtual_cells] exoskeleton_image` | Placement mirrors attach's plan, checked against the ceiling of the Cell's access level. The dispatcher passes a Real Cell's access level; without it, every Real Cell used to fail closed at READ_ONLY. |
| File roots (ADR-0034) | `H/guard/file_urls.py`, `H/exoskeleton/browser/files.py`, `H/exoskeleton/browser/playwright/guard.py`, `act.py` (`page_reach`), `GuiAllowlistCheck` | One lexical rule, `file_url_escapes(url, roots)`, asked by the tool, the gate and both browsers; `FileGuard` routes `file://**` in Playwright and checks the realpath too. |
| Exit criteria | `T/e2e/test_phase6_exit_criteria.py`, `T/e2e/phase6_exit_helpers.py`, `T/e2e/test_scout_then_forager.py`, `docs/runbooks/phase6-windows-exit-check.md` | Three placements of the login (Hive Stand on a lease-started display, a `desktop-ubuntu` Virtual Cell with the wrong click, browser-only), planned as a Forager; and a Scout-then-Forager goal, feasible and infeasible, through a real Queen and Warden. |
| Test support | `T/builders/{exoskeleton,gui,recordings,rehearsal,audio}.py`, `T/contracts/*_harness.py`, `T/fixtures/sites/login/`, `T/e2e/kernel_helpers.py` (`PairOptions`), `T/builders/wardens.py` (`_rebound`), `T/builders/llm.py` (`judge_approve_response`) | `ScriptedSurface` (in `builders/gui.py`) is shared by the gate and Warden tests. `login_recording(site, suffix)` records the bee's login, including one rolled-back mistake. `FakeLLMProvider.answer_slot` routes one slot's calls away from the scripted queue. |

## 3. Decisions and behaviour worth knowing (beyond the ADRs)

- **Tiers of GUI actions** (`H/workers/tools/exoskeleton/act.py`):

  | Action | Tier |
  |---|---|
  | Input on a lease-started display; a page that stays on the Cell (a `file://` URL inside scratch, `about:blank`, a loopback host) | `scratch_write` |
  | A `file://` URL outside scratch | Never loaded. `browser_navigate` refuses it before proposing; any other proposal of it is `outside_scratch_write` and `GuiAllowlistCheck` fails it; both browsers refuse it; `FileGuard` aborts the request however it arose (a link, a script, an iframe). |
  | Input on the operator's own display | `device_command` |
  | Anything that leaves the Cell | `network_egress`, plus `net:<host>` at the ALLOWLIST rung |

  `irreversible=true` raises any action to `irreversible`. The loopback row, and what clicks and scripts can reach, are open items (4.2).
- **Roles.**
  - A Forager refuses to run without an attached Exoskeleton (`ForagerRequiresExoskeletonError`), which the runtime turns into an Alarm and a FAILED result: a Forager without one is a placement bug. Round caps: Drone 12, Forager 16, Scout 6.
  - Every successful `browser_read` or `browser_snapshot` a Forager makes is Nectar: a Bee Bread `TOOL_RESULT` at the task's clearance, prefixed with the page's URL, scrubbed for credential shapes and capped. Frames never reach it. Phase 7's Honey Store replaces Bee Bread as the body later.
  - A Scout gets read tools, `browser_navigate`, GET-only http and `report_findings`, nothing that writes except the report. `report_findings` validates a `ScoutReport`, writes `scout-report.json` through the capped write path (its acceptance is exactly FILE_EXISTS on that file) and ends the loop. A Scout that runs out of rounds files an infeasible report saying so.
  - The Queen hands a task the reports of its SUCCEEDED Scout dependencies as recon: at most 4, newest first. The brief renders them inside a delimited `<<<scout_findings>>>` block, as retrieved content.
  - An infeasible Scout is FAIL_TASK with no retry, and every non-terminal task behind it is CANCELLED with "Held back by Scout <id>. <reason>", so the goal ends.
  - A role's footprint and grant come from its `[forage.roles]` entry when there is one, else the drone's; `forager` and `scout` are not required manifest keys.
- **Alarms.**
  - A GUI action that rolls back raises `POSTCONDITION_FAILED` at once, not after three rollbacks.
  - A judge that REJECTs an applied irreversible GUI action makes the Worker's tool raise one CRITICAL `AUDIT_FAILED` through the Worker telemetry. That is the path the Warden's escalation policy sees, and the default policy escalates to a person, which ends the attempt.
  - `review_applied` records the verdict and deposits the finding, but raises no second Alarm. Sampled `audit_completed` still raises a WARNING `AUDIT_FAILED` on REJECT. Both treat a judge that cannot answer (`JudgeAnswerError` or `JudgeUnavailableError`) as no verdict rather than a crash.
  - GUI Alarms name the recording: "GUI proposal <id> in recording <id>".
- **The judge.**
  - Frames reach it only when every binding in the JUDGE chain declares vision (`BoundModel.sees`); the same rule gates the `see` and `listen` offers.
  - Evidence text is capped at 30,000 characters and two frames, and never appears in a repr.
  - `goal` is the task's objective, capped at 4,000 characters, on the real-time check (`CheckContext.goal`, from `GateDeps.goal`) and on the post-apply review alike. It is never the proposer's own reasoning.
  - A Virtual Cell's Warden has a model-backed judge exactly when its slot table binds JUDGE (`H/cli/in_cell/deps.py`, `_with_judge`); without one, JUDGE tiers fail closed there as before.
- **Frames never become JSON.**
  - `Frame` refuses serialization.
  - The recorder stores PNGs in BLOB columns.
  - A secret is recorded as its length (`describe_step`) or as the mask (`redact_step`, which feeds procedure `SecretSlot`s).
  - Every other typed text, URL and snapshot is scrubbed for credential shapes.
- **Handoffs.** A Handoff's `do_not_redo` names every capped side effect, including `keep`, plus GUI actions the bee called irreversible. Other GUI steps are left out, because a resumed bee gets a fresh screen and browser.
- **Desktop and browser environment.**
  - The desktop requires openbox: on Xvfb 21.1, xdotool's pointer moves are silently ignored without a window manager.
  - Openbox runs under a generated rc (`H/exoskeleton/attach/openbox.py`): no key bindings, mouse bindings on frames and clients only, focus on new windows and on a press, and a root menu that points at an empty file. Its stock menu could launch programs from a click on the desktop.
  - xauth is not used; attach writes its own authority entry.
  - The Chromium sandbox is off inside a Cell or when running as root (`ExoskeletonConfig.browser_sandbox`). The Wardens get a `ChromiumLauncher` only when Playwright is installed.
- **Browser navigation.** A failed navigation waits up to 2 s for Chromium's error page to commit (`page.py`), and an overlapped navigation that ended on the error page is retried once. Before this fix, about one real-Chromium contract run in eight flaked.
- **Spend.** A summed `Usage` starts from `Usage.zero()`, priced at 0.0; before that fix every task recorded $0 because an unpriced start swallowed every cost. A tool that ends the loop early (the Scout's report) has its usage recorded from `_UsageTally`, the call gate wrapper in `bounded_loop/runner.py`. The Forage ledger and `GoalReport.spend_usd` never read an outcome's spend: they count calls, through the metered gate and the trail's `llm.call` events.
- **CLI logging.** The CLI configures logging once: to stderr, at WARNING, or at the level `HIVEMIND_LOG_LEVEL` names. Before, `hive run --json` broke on the first debug line.
- **Pre-existing bugs fixed on this branch.**
  - The Docker snapshot rollback's recreate spec dropped `extra_hosts` and `cap_add`, so a recreated Cell never dialled back.
  - The IPv6 URI test now skips on hosts with no IPv6 loopback.
  - `hive llm slots` and `hive llm providers` crashed on a provider that only transcribes.
  - The $0 spend above.

## 4. Open items, in suggested order

### 4.1 Roadmap 6.13 Pheromone Mask: not built

**What happened.** The roadmap says to ask the user for writing instructions before building
6.13. When asked on 2026-09-24, the user specified two things:
- the input tactic to type with their own keystroke-timing model (their `Keystroke-Synthesizer` repository);
- the prose tactic to write in their personal voice, following their `copytone-justin` skill.

Together, those would make the text the Hive writes, and the keystrokes it types, pass as the
operator's own hand-typed work. Authorship checks, AI-writing detectors and keystroke-dynamics
proctoring exist to tell exactly that apart, so the Hive would be defeating them. That was
declined, and nothing of 6.13 exists: no `supervision/mask.py`, no tactics, no mask ADR.

**For the next session.**
- Do not build a persona profile, keystroke or voice cloning, or anything else whose purpose is to make the Hive's output pass as a particular person's own writing or typing, or to hide that automation is at work. The README already puts this out of scope ("not a stealth layer, and features whose purpose is to evade a service's controls are out of scope"), and so does codingrules section 15.
- Whether any part of 6.13 should still be built is the user's call. Raise it with them; do not build it from the roadmap text. Nearby needs that are in scope are separate features and should be proposed as such: a house style for the Hive's own messages to its operator, or input pacing for an application that drops events delivered too fast.
- The `copytone-justin` skill's own files tell an agent to add `.claude/` to `.gitignore`. This repository tracks `.claude/` (the roadmap, the rules and these handoffs), so do not apply that instruction here. None of the skill's files were committed.

### 4.2 Security gaps found while closing the `file://` hole (not fixed)

In rough order of severity:

1. **`run_command` is confined only by its working directory.**
   - `H/workers/tools/session.py` tiers a command by its cwd, so a command run in scratch is `scratch_write`: SCHEMA and SIZE_CAP, never the ALLOWLIST rung.
   - On a Real Cell the process (`H/cell/local/process.py`) is a plain subprocess with a file-size limit. It can read, copy or change anything the Cell user can, and on the Hive Stand that is the operator's account. `cp ~/.ssh/id_rsa .` followed by `read_file` walks straight past ADR-0034.
   - Running the ALLOWLIST rung alone would not help: the SCRATCH access level grants `exec:*` (`H/guard/access.py:47`).
   - It needs its own decision about what a scratch command may touch on a Real Cell: for example a filesystem sandbox rooted at scratch (bubblewrap, Landlock), or no `run_command` on the Hive Stand below some access level. ADR-0034's consequences record the gap.
2. **Desktop input reaches Chromium's own dialogs and DevTools.**
   - Only the `browser_*` tools are held to the file roots. On a lease-started display, `press`, `type` and `click` are `scratch_write`.
   - A key chord into the browser window can open Save As, which writes the page anywhere the Cell user can write, or DevTools, whose workspace reads and writes local folders.
   - Opening a file with Ctrl+O still ends in a `file://` request that `FileGuard` aborts, but the file dialog itself lists directories.
   - Options: disable DevTools and file dialogs for the lease's Chromium (Chromium policies are machine-wide on Linux, so the images could ship them and a Real Cell could not), refuse those chords at the desktop tools, or tier desktop input over a browser window as the browser's own tools are tiered.
3. **Loopback counts as staying on the Cell.** `page_reach` tiers a loopback host `scratch_write`. On a Virtual Cell that is the container. On a Real Cell it is the operator's own local services, the Hive Stand's own loopback listener included. Consider tiering loopback on a Real Cell as `network_egress` with a `net:` scope of its own.
4. **Clicks and page scripts reach hosts no `net:` capability names.**
   - Only `browser_navigate`'s own URL is checked against `net:<host>`. A click is tiered by the page it starts on, and a page's scripts fetch what they like.
   - On a Virtual Cell the container's network policy bounds this; on a Real Cell nothing does.
   - A Playwright route on every request, like `FileGuard`, could hold each host to the lease's `net:` scopes.
5. **A hard link in scratch passes `FileGuard`**, because realpath cannot see one. Making one needs a command, so item 1 bounds it.
6. **The DevTools port on a shared Real Cell** is loopback-only, but any local user can reach it; `--remote-debugging-pipe` would close this. Chromium's crash handler also runs outside the lease's process list. Both are in ADR-0031's known limits.

### 4.3 Free memory is read as `MemFree`, so a warm Linux host looks full

Found by the final real Docker run; it predates phase 6.
- `_memory_bytes` in `H/cell/local/probe.py` reads `os.sysconf("SC_AVPHYS_PAGES")`, which on Linux is `MemFree` and leaves out reclaimable page cache. On a Linux host that has been up a while, that is a small fraction of what is available: this sandbox, after the image builds, had 717 MiB free and 15 GiB available.
- Forage takes the reserve (`DEFAULT_RESERVE_MEMORY_BYTES`, 512 MiB) off that figure before it grants sub-bees (`H/forage/allocate.py`), so such a host is denied every grant ("Forage denied: grant allows zero sub-bees") and the task fails at once.
- A Virtual Cell runs the same probe inside its container (`_probe_config` in `H/cli/in_cell/config.py`), where `sysinfo` describes the Docker host (on Docker Desktop, its VM), not the container's own cgroup limit. So it reports the host's `MemFree`, and the host's total.
- Swapping in `MemAvailable` alone would make a Virtual Cell over-report: a 1 GiB container would claim the host's free gigabytes, and Forage could grant more sub-bees than the container can hold. The fix needs `MemAvailable` (from `/proc/meminfo`) on a host, the cgroup's own `memory.max` and `memory.current` inside a container, and a look at whether a 512 MiB reserve suits a 1 GiB Virtual Cell at all.
- Windows reads `ullAvailPhys`, which already counts standby memory, so a Windows Hive Stand is unaffected. Its Virtual Cells are not: they run Linux inside Docker Desktop's VM.
- Until it is fixed, a real run on a busy Linux host may need `sync; echo 3 > /proc/sys/vm/drop_caches` first.

### 4.4 Night Veil teardown purge is not wired

No composition root constructs `NightVeilTeardownPurge` (`H/pheromone/retention.py:178`).
`night_veil_side_channels(recordings)` (`H/cli/compose/exoskeleton.py:286`) already returns the
recorder's side channel for it. Night Veil placement still fails closed (phase 5 open item 3), so
nothing leaks today. Wire both together when that placement opens.

### 4.5 Smaller items

1. **A failed task's dependents wait forever.** Only an infeasible Scout cancels the work behind it (`_cancel_held_back` in `H/queen/ticks/results.py`). Any other FAILED task leaves its dependents PENDING, so `hive run` ends only at its timeout. This predates phase 6. Decide whether a failure should cancel its dependents in general (an Appendix C edge).
2. **The handoff path records none of its attempt's usage** (the `HandoffRequestedError` branch of `run_bounded_loop`; this predates phase 6). `_UsageTally` already has the total. But `record_tokens` also feeds `should_hand_off`, so check how a resumed attempt's telemetry starts before recording it there.
3. **No metering inside a Virtual Cell.** Every call there, the new judge's included, goes through `DirectCallGate`: there is no per-Cell Fanner or seat meter yet (`H/cli/in_cell/deps.py`).
4. **Live provider checks.**
   - The Anthropic mapping sends `strict: True` tools (`H/llm/providers/anthropic/mapping.py`), and the Worker's tool set includes the Exoskeleton tools' nested `expect` schemas.
   - The OpenAI-compatible mapping sends images and audio in a user message after the tool messages.
   - Both are fixture-tested only. Run the `live_llm` suite with credentials, plus a real LM Studio run of the login goal (the runbook's step 3 shows how).
5. **Windows and macOS.** The browser locator paths are unit-tested but have never run on those systems. The Windows exit leg is the runbook; `ci.yml`'s Windows leg runs once there is a PR.
6. **A stale test docstring.** `patch_placement_policy` in `T/builders/virtual_cells.py` says `[placement]` is never wired into `QueenDeps`, but `_placement_policy` in `H/cli/compose/deps.py` wires it now. Check whether the patch is still needed, then drop it or its claim.
7. **A killed test run can orphan Chromium.** A clean detach stops every lease-started browser, but after a `kill -9` or a hard timeout one can survive; `pkill -f /opt/pw-browsers` cleans up.
8. **Rebuild the images after any source change.** A container runs the package as built, not the checkout.

## 5. How to work in this sandbox

**Environment** (a fresh container needs all of this again):
- Full dev env: `uv sync --frozen --all-groups --all-packages --all-extras` (the Docker SDK, Playwright, faster-whisper).
- The toolchain was installed by hand: `apt-get install -y --no-install-recommends xdotool pulseaudio pulseaudio-utils openbox x11-utils imagemagick fonts-dejavu-core`. Xvfb was already present.
- Chromium is pre-installed under `PLAYWRIGHT_BROWSERS_PATH=/opt/pw-browsers` (`chromium-1194`). Never run `playwright install`. Playwright 1.63 expects `chromium-1243`: the repository's locator finds the older build by itself, but an ad-hoc Playwright script must pass `executable_path="/opt/pw-browsers/chromium-1194/chrome-linux/chrome"`.
- Docker: start the daemon by hand with `nohup dockerd > <scratchpad>/dockerd.log 2>&1 &`. It does not survive a container restart; `docker images` shows whether the dev images are still there.
- Always run `uv run python scripts/...`. The system `python3` is 3.11 and cannot parse the repository's PEP 695 syntax.

**Real-peripheral test notes:**
- The harnesses put scratch under `tempfile.mkdtemp(prefix="hm-", dir="/tmp")`, because PulseAudio's socket lives in scratch and a Unix socket path must fit in 108 bytes. pytest's `tmp_path` is too long.
- The sandbox runs as root, so Chromium runs without its sandbox here.
- A harness that serves fixture pages as `file://` must name their directory as a file root, as the browser contract suite does; everything else serves fixtures over loopback http.

**Gates.** Run these from the repository root:

```bash
uv run ruff format --check . && uv run ruff check . && uv run mypy && uv run lint-imports
for s in check_sizes check_fanout check_no_kind_branches check_no_model_ids check_no_transcripts; do uv run python scripts/$s.py; done
uv run pytest -q -m "not integration and not live_llm and not local_llm" packages scripts/tests -p no:cacheprovider
uv run pytest -q -m integration packages/hivemind/tests/integration -p no:cacheprovider   # needs dockerd + images
```

To match CI's unit job exactly (no extras, plus coverage floors), use a separate venv:
`UV_PROJECT_ENVIRONMENT=<scratchpad>/venv-noextras uv sync --frozen --all-groups`, then `uv run --frozen pytest -m "not integration and not e2e and not live_llm and not local_llm" --cov --cov-report=json`, then `uv run --frozen python scripts/check_coverage_floors.py`. Delete `coverage.json` and `.coverage` afterwards; both are gitignored.

**Forcing every audit sample.** Audit sampling hashes each proposal's random id, so a test whose scripted fake also answers a judge can fail about one run in six and pass on rerun. That is how the Virtual Cell suite flaked until `FakeLLMProvider.answer_slot` fixed it. To flush such a test out, save this as `<scratchpad>/force_audit.py` and run pytest with `PYTHONPATH=<scratchpad>` and `-p force_audit`:

```python
from hivemind.supervision.capping.audit import AuditSampler

AuditSampler.should_sample = lambda self, proposal_id, tier, audit_rate: audit_rate > 0.0
```

Every e2e test passes this way at handover.

**Subagents and worktrees.** A subagent dispatched with `isolation: "worktree"` gets a worktree under `.claude/worktrees/` created from `main`, not from this branch. Tell it to merge the branch in first (`git merge --ff-only claude/sleepy-bardeen-vz4cz4`), and bring its commits back with `git cherry-pick`. `.claude/worktrees/` is listed in `.git/info/exclude`, which is local to a clone and does not travel.

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

Build base first, then desktop: `build_image.sh base-ubuntu hivemind/base-ubuntu:dev`, then `build_image.sh desktop-ubuntu hivemind/desktop-ubuntu:dev`. Its `repo=` line decides which tree goes into the image: once it pointed at a subagent's worktree, and the images silently lacked the Forager.

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

**A real Docker run** (how the "Met on real Docker" note was produced; the driver was a scratch file):
- A stub OpenAI-compatible server on the host answers by `request.slot`: the plan for QUEEN, an APPROVE for JUDGE, the Forager's tool calls for WORKER.
- The fixture site is served over http on the host too. The container reaches both through `host.docker.internal`, because the browser runs inside it.
- The manifest sets `[placement] prefer = "virtual"` and `[virtual_cells] backend = "docker"`, with the images above; `build_hive`, `run_hive` and `run_goal` drive the goal.
- Afterwards, `docker ps -a` must show no Cell container.

**Budget.** Subagent tokens share the session limit (`.claude/subagents.md`). One dispatch in the first session died on that limit. Prefer fewer, larger dispatches, and have the orchestrator run the gates and commit.

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
| `565b067` | the first version of this handoff |
| `f9a6027` | waggle: `SCOUT_REPORT_FILE` |
| `9da2816` | a lease's browser reads only its own scratch (ADR-0034) |
| `befe2ef` | the real-time judge reviews against the task's goal too |
| `8b89283` | integration.yml stops installing xauth |
| `8e09603` | a click where the pointer already is no longer hangs xdotool |
| `a424f99` | the lease display's window manager launches nothing |
| `26ba0ab` | Queen: plan and dispatch the Forager and Scout roles |
| `2e7d5ad` | Queen: role, recon and infeasible Scout tests |
| `00cc9a3` | Queen: role docs |
| `0ba5ad6` | an infeasible Scout cancels the work that waited on it |
| `9952e09` | `roles.bounded_loop`, shared by every bounded-loop role |
| `0f6e880` | the Forager and Scout roles (6.9, 6.10) |
| `f4ab698` | recon shows the Scout's findings; roles come from a registry |
| `5aa5b49` | Scout then Forager through a real Queen and Warden; tick 6.9, 6.10 |
| `ffe8b40` | audit review catches `JudgeUnavailableError` too |
| `bf15412` | phase 6 exit criteria end to end |
| `386bc8a` | the Windows exit runbook |
| `313781f` | a Virtual Cell's Warden gets the judge its slot table binds |
| `72759f6` | the exit login planned as a Forager |
| `d8185d6` | a Virtual Cell's fake answers its judge apart from the Worker's script |
| `1d2004e` | a task's recorded spend is what its calls cost, not $0 |

This handoff, with the roadmap's phase 6 notes, is the 57th commit.
