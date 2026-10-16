# Bug review of the phase 8, 9, 11 and 12 branches (2026-10-16)

> A read-only review of the agent-written code that the five `ccr-*` branches brought to `main`.
> Nothing in the tree was changed by this review. Every finding below carries a HEAD line number,
> a concrete path to the failure and a fix sketch; the next session works them in the order given.

## Scope and method

The five branches (`ccr-0235b6d7` phase 8, `ccr-4e10bf6f` phase 9, `ccr-c966d521` phase 12,
`ccr-fc6c1f6f` the merge of those three plus Windows portability, `ccr-75a88a63` empty) were
already merged into `main` at 744efae7 when the review started, so the review target was the
diff from their common base, commit 4b66406, to `main`: 304 commits, about 80k added lines of
Python and 36k of TypeScript.

Nine reviewers each took one subsystem and read the diff in full, read the current files where a
hunk was not self-explanatory, ran the existing unit tests for their area and wrote throwaway
reproductions where a finding needed one. The subsystems: `wardens`; `queen`; `swarm` +
`entrance` + `waggle`; `pollen` + `cell` + `hive`; `royal_jelly` + `basket` + `workers`; `cli` +
`manifest`; `llm` + `forage` + `observation` + the small diffs in `memory`, `honey_store`,
`pheromone`, `guard`, `exoskeleton`, `supervision`, `common`, `brood_chamber`; `observation-web`
views; `observation-web` client, store, shell, PWA and Android. Their full reports follow the
ranked list.

Baseline on `main` before the review: `ruff check`, `ruff format --check`, the size, fan-out and
kind-branch hygiene scripts, `lint-imports` and `mypy` (3436 files) all pass; the unit suites each
reviewer ran pass; `pnpm typecheck`, `pnpm lint`, `pnpm generate:check` and `pnpm test` (888
tests) pass for the front end. One housekeeping note: `lint-imports` warns that two `subprocess`
ignore rules in the import-linter config (`hivemind.hive.backends.**` and
`hivemind.royal_jelly.quarantine_comb.sandbox_subprocess`) no longer match any import, so the
allowlist is stale and should be updated to the modules that actually import `subprocess` now.

Independently re-run by the orchestrator: the sandbox rename escape (royal_jelly F1), the swarm
gate transition race (queen F1), and the routing floor probe (llm F2) all reproduce at HEAD; the
step-up 401 path (web shell F1), the PROPOLIS colonization mismatch (cli F1) and the Anthropic
cache-token pricing gap (llm F1) were confirmed by reading both sides of each seam.

## Findings, ranked

44 findings in all: 0 CRITICAL, 6 HIGH, 11 MEDIUM, 27 LOW. The six HIGH ones are the pass the
tree needs before the next phase; the MEDIUM ones are worth the same pass where they share a file.

### HIGH

1. **A quarantined tool can move a file out of its sandbox undetected** (royal_jelly F1,
   reproduced). `quarantine_comb/runner/hooks.py:199` checks only the first argument of a path
   audit event, so `os.rename`/`os.replace` from scratch to any outside path passes every gate;
   `os.link`/`os.symlink` are not audited at all and none of these are statically banned. The tool
   is then promoted and runs unsandboxed in a real Worker.
2. **A Brood Chamber transition conflict tears the whole Hive down** (queen F1, reproduced).
   `cluster/protocol.py:266` pauses a task after a 30 s wait without re-reading it, and
   `swarm_gate/colonize/steps.py:348` resumes without checking PAUSED; the resulting
   `InvalidTransitionError` is not in the swarm gate's recoverable set, so a task finishing during
   a colonization or renewal cancels every device Warden and then the Queen.
3. **A colonized device's Warden can never ship a Basket blob up** (queen F2). The Queen's
   transfer verifier (`ticks/tools/transfer/receive.py:249`) only knows Virtual Cell uplink keys;
   a device Warden signs with its node key and every offer is refused `UNKNOWN_SIGNER`. Phase 9's
   `retain` and cell-scope tool requests do not work from any device above Level 0.
4. **A PROPOLIS device can never be colonized, and the Hive retries forever** (cli F1). The
   borrowed Warden refuses every tier but MEADOW (`cli/in_cell/borrowed/config.py:269`), the
   packet passes the node's tier through, and `swarm/level.py:254` never consults the tier; the
   START step fails and the rung keeper re-schedules the same doomed move on every pass.
5. **Anthropic cache reads and writes are never priced** (llm F1). The adapter maps
   `input_tokens` to the API's non-cached count and drops `cache_creation_input_tokens`
   (`providers/anthropic/mapping.py:455`), while `fanner/pricing.py:88` bills only
   `input_tokens`; per-goal spend caps, `GET /v1/cost` and the Prometheus total all understate the
   bill on exactly the cached Queen prompts.
6. **A mistyped step-up password logs the device out and orphans the server session** (web shell
   F1, reproduced). `landing_board/client/http.ts:160` treats every 401 on a signed request as
   session loss, but the Entrance answers a wrong step-up password with 401 while keeping the
   session.

### MEDIUM

7. A returning device's Warden is told to cancel a task the dispatcher just re-placed on it
   (queen F3): the attach releases the hold before the gate's `found` sends the cancels.
8. `session.put_file` enforces the scratch quota only on the first chunk (pollen F1): nothing caps
   `written` at the declared total, so a sender can grow a file without bound.
9. Windows process trees are killed with `taskkill /T`, not the Job Object the contract names
   (pollen F2); a detached grandchild survives release and `group_alive` cannot see it.
10. A Basket shipment up is lost for good when the Queen link blips (wardens F1): `basket.offer`
    and `basket.chunk` are refused offline, the False is discarded, and nothing re-offers.
11. A device manifest's `[llm.floors]` is validated and then silently dropped (cli F2): the
    borrowed Warden routes on the default floors.
12. Routing's grade floor is capped at the strongest source on the whole map, not in the chain
    (llm F2, reproduced): an off-chain grade-5 model makes a CRITICAL task fall back to the
    manifest primary with no shortfall filed.
13. The Fanner's spill rule uses the uncapped floor (llm F3): on a Hive whose best model is below
    the floor every CRITICAL call spills to the weakest link.
14. A tainted Bee Bread entry answers 403 naming the tainting event instead of 404 (llm F4): taint
    status is probeable through the Honey browser.
15. `OverlayingProvider` hides `list_models` (llm F5): `hive llm models` refuses any non-Ollama
    provider with a `prompt_overlay`.
16. A UnifiedPush endpoint rotation is dropped on Android (web shell F2); push dies silently.
17. A 4401 on a stream reconnect leaves a dead session on screen as "no access" (web shell F3).
18. The waiting notice misses notices across a reconnect and is never re-seeded (web shell F4).
19. Push-to-talk keeps recording and posts a clip after the Chat view unmounts (web views F1,
    reproduced): a hold still opening the microphone is not cancelled by the cleanup.

### LOW

20. The resume pauses never wake the Warden tick; tasks resume up to one heartbeat late (wardens F2).
21. A bee that checkpoints after the pause resumes from the older Handoff (wardens F3).
22. A Nuc promotion job and a TaskAssign can lease the device twice, raising a false
    CELL_UNREACHABLE (wardens F4).
23. `ChangeTracker` drops file changes past `cap` for good (wardens F5).
24. A refusal arriving after 2 s installs a tool the Queen refused (wardens F6).
25. `Promotion` opens the Cell to the dispatcher before the move is committed (queen F4).
26. A freshly attached sandbox Cell is caught up on every hive-scope tool (queen F5).
27. A Level 0 rebuild pauses bees with no Handoff patience (queen F6).
28. The output cap can be overshot when stdout and stderr fill concurrently (pollen F3).
29. A lease-table write failure in `_open` leaves an orphan scratch directory (pollen F4).
30. `ResidentBasket.admit` is a non-atomic read-evict-mark; concurrent admissions overshoot the
    disk ceiling (royal_jelly F2).
31. `RLIMIT_AS` is built from a resident budget, so a VSZ-heavy tool is killed and misreported
    (royal_jelly F3).
32. A device lease opened on the device is lost if the Hive's `cell.leased` trail write fails
    (swarm F1).
33. `admit` racing a dying old link can leave a connected device UNREACHABLE until its next
    heartbeat (swarm F2).
34. Unauthenticated enrolment attempts each cost a trail row with no per-source limit (swarm F3).
35. `certificate_pin` reads the PEM as ASCII and fails on a non-ASCII comment (swarm F4).
36. `run_in_cell_warden` leaks a connected Queen link when the build fails after connect (cli F3).
37. The borrowed Warden's SQLite stores are never closed and the outbox is opened on the loop
    (cli F4).
38. `OllamaManager.detect` raises raw httpx errors for a scheme-less `base_url` and leaks its
    client (llm F6).
39. The Cost view counts amortised local spend as hosted (llm F7).
40. Streamed usage on the finish chunk is ignored and replaced by an estimate (llm F8).
41. View and summary state survives logout; the next login shows stale counts (web shell F5).
42. A step-up done from the Chat never clears `needsStepUp` (web views F2).
43. The Chat hides its only step-up prompt on a `step_up_required` read refusal (web views F3).
44. The Thoughts view's "open this view again" cannot be followed (web views F4).

## Suggested order of work

Fix 1 first (security boundary, reproduced, small). Then 2, 3 and 4 together, since all three sit
on the swarm gate and the device Warden seam and want one e2e scenario that ships a blob from a
colonized device and one that colonizes a PROPOLIS node. Then 5 and 6, each a one-file change
with a unit test. The MEDIUM list groups naturally by file with those.

---


<!-- full report: royal_jelly -->

## Full report: Royal Jelly Lab / Comb Registry / Basket review — 3 findings

### Summary
Phase 9 lets the Hive extend itself: scaffold a tool, prove it in the Quarantine Comb's sandbox,
then promote it into the Comb Registry and ship it as an inert, signed, content-addressed Basket
blob. The code is careful and well-layered. Content addressing is anchored to a signed manifest at
every hop (deposit, transfer, worker load), the registry's state machine and SQLite triggers make
versions immutable and bypass-proof, and the promotion flow cannot skip a gate or promote without a
passing report. The risk concentrates in the one place untrusted model-written code is judged: the
in-sandbox runner's `sys.addaudithook` policy, which is the sole mechanism enforcing "a quarantined
tool cannot write outside its sandbox". I found a real, reproduced gap there (F1). The Basket and
registry are otherwise sound; the two remaining findings are lower-severity accounting/limit issues.
Shippable after F1 is closed.

### Findings

#### F1. Audit hook never checks the DESTINATION of a rename/replace/link, so a tool can write outside its sandbox undetected   [severity: HIGH] [confidence: HIGH]
- File: packages/hivemind/src/hivemind/royal_jelly/quarantine_comb/runner/hooks.py:199-201 (and the
  `_PATH_EVENTS` set at :63-75); banned-list counterpart gates/banned.py:76-100 and
  `PATHLIB_IO_ATTRIBUTES` at gates/banned.py:146-148.
- What: `_forbidden_what` only inspects `args[0]` of a path event. For `os.rename`/`os.replace`
  (CPython emits the audit event `os.rename` for both, and `pathlib.Path.rename`/`.replace` route
  through them) that is the SOURCE; the DESTINATION (`args[1]`) is never resolved or checked against
  the allowed roots. `os.rename` is also not in the static banned-name list, and `rename`/`replace`
  are not in `PATHLIB_IO_ATTRIBUTES`, so both the static gate and the runtime hook let them through.
  `os.link`/`os.symlink` are neither in `_PATH_EVENTS` nor statically banned at all.
- Path to failure: a scaffolded `tool.py` writes a file into scratch through the legitimate
  session (an `fs:write` call it declares), then calls `os.rename(scratch_file, "/path/outside")`.
  The `os.rename` audit event fires with `args[0]` = the in-scratch source (allowed), so no
  violation is recorded; the move to the out-of-scope destination is invisible to the Comb. Every
  gate passes. The Comb's job is to prove the tool only touches its package and scratch before the
  tool is promoted and then run UNSANDBOXED inside a real Worker process (no audit hook there), so
  the out-of-scratch write executes for real on the Worker's machine with the Worker's privileges.
- Evidence: `if event in _PATH_EVENTS and args: path = _resolved(args[0])` — only `args[0]`.
  Reproduced with the subprocess sandbox (.claude/bug-review-2026-10-16/royal_jelly/repro.py.txt): a tool that
  `session.put_file`s into scratch then `os.rename`s to the host temp dir returns
  `PASSED: True, failing_gate: None`, all six gates pass, and "escape file exists after run: True"
  — the file physically escaped the sandbox to a path under no allowed root.
- Fix sketch: in `_forbidden_what`, for the rename/link family check every path argument
  (`args[0]` and `args[1]`) against `_is_allowed`; add `os.symlink`/`os.link` to `_PATH_EVENTS`;
  and add `os.rename`/`os.replace`/`link`/`symlink` to the static `os` banned-name list and
  `rename`/`replace`/`symlink_to`/`hardlink_to` to `PATHLIB_IO_ATTRIBUTES` so a direct move is
  caught statically too.

#### F2. `ResidentBasket.admit` is a non-atomic read-evict-mark, so concurrent admissions can overshoot the disk ceiling   [severity: LOW] [confidence: MEDIUM]
- File: packages/hivemind/src/hivemind/basket/residency.py:112-134 (`admit`), with the receiver
  deciding on `plan` at offer time (transfer/receiver.py:200-214).
- What: `plan` (offer time) and `admit` (after transfer) each independently read `store.resident`,
  compute evictions and then `mark_resident`, with awaits between and no cross-call lock. Two
  transfers to the same Cell can both observe room, both transfer, and both mark resident, leaving
  the resident total above `basket_disk_mb`.
- Path to failure: Cell near its ceiling; two blobs are offered close together. Both `plan` calls
  see the same resident set with room for one; both are accepted and transferred; both `admit`
  calls evict based on the same stale snapshot and mark their blob resident -> ceiling exceeded
  until the next eviction pass.
- Evidence: `admit` does `resident = await self._store.resident(...)` then `await self._store.evict`
  / `await self._store.mark_resident` as separate statements; `mark_resident`/`evict` each open
  their own `transaction()` (store/sql/residency.py), so nothing serialises the plan.
- Fix sketch: compute evictions and insert the new residency row inside one store transaction (or
  take a per-Cell lock around admit), re-reading the resident total under the write lock.

#### F3. `RLIMIT_AS` is set from virtual size + the RESIDENT memory budget, so a VSZ-heavy but RSS-light tool is killed and charged to the resources gate   [severity: LOW] [confidence: MEDIUM]
- File: packages/hivemind/src/hivemind/royal_jelly/quarantine_comb/limits.py:92-97.
- What: `RLIMIT_AS = baseline.address_space_bytes + limits.memory_bytes`, i.e. an address-space
  (virtual) cap built by adding a RESIDENT budget. The resources gate judges `peak_rss_bytes`, but
  the kernel enforces AS. A tool that reserves virtual address space well above its RSS (a thread
  pool — ~8 MiB stack reservation each — , a large mmap of a scratch file, or glibc arena
  over-reservation) can exceed `baseline_vsz + memory_bytes` in VSZ while its RSS stays within
  budget, get SIGKILL/ENOMEM, and be reported as "killed ... most likely by a CPU, memory or file
  limit" (comb.py `_runner_died`).
- Path to failure: an honest tool whose memory_bytes budget is, say, 256 MiB spawns a handful of
  threads via the session or maps a moderate scratch file; VSZ crosses the AS cap though RSS is
  tens of MiB; the run dies and the resources gate fails a tool that was within its real budget.
- Evidence: the limit math adds a resident budget to a virtual baseline; `apply_limits` sets
  soft==hard so it is a hard AS cap (SIGKILL), and the gate's stated unit is `peak_rss_bytes`.
- Fix sketch: either drop `RLIMIT_AS` and rely on the measured RSS gate (plus the container's own
  memory cap), or size the AS headroom generously (a multiple of the RSS budget) rather than 1:1.

### Looked at and found sound
- Content addressing and inertness: deposit (deposits.py) and store (store/sql/blobs.py
  `_check_bytes`) both re-hash bytes and reject a manifest describing another blob; transfer
  receiver verifies the manifest signature then the whole-blob SHA-256 before finalising, and
  `ResidentBasket.bytes_for` re-hashes on the way out. `offer_from_wire` rejects any claim field
  that disagrees with the signed manifest. A blob is only ever executable through a registry entry
  the Comb produced; raw blobs are never imported.
- Chunk transfer: strict in-order offsets, `too_large` past the declared size, idempotent resend of
  an acked region, resume offset discarded if longer than the blob, whole-file hash the only thing
  that lets bytes be kept; a wrong prefix just fails the final hash and is discarded.
- Transfer and tool state machines (transfer/state.py, tool_state.py): read-only transition tables,
  every move checked, terminal states have no exits.
- Comb promotion flow (promotion/flow.py): no gate skip; a failing report rejects; REAL_CELL-path
  HIVE promotion needs review and, absent a reviewer, the registry's own `NeedsReviewError` still
  refuses; one retry only.
- Comb Registry (rules.py, writes.py, sql.py, migration): authority matrix (HIVE=Queen, CELL=owning
  Warden, Queen may only retire/rollback at CELL), duplicate-version refusal re-checked under the
  write lock, one-current unique index, immutability/no-delete/retired-is-final SQLite triggers,
  rollback re-checks the stored report. No double-install (unique index) and no promote without a
  matching passing report.
- Uninstall-while-in-use: `LoopExecutor._raise_if_tools_changed` fires before running a call when
  the bundle version changed, and `CombToolRunner._refusal` re-checks `entry in bundle.entries()`
  at call time (defence in depth).
- Package unpack (spec/package.py): zip-bomb, duplicate-entry, encryption, codec, path-traversal
  (backslash/NUL/absolute/`..`) and declared-vs-actual size all bounded.
- Worker loader (workers/tools/comb/loader.py, invoke.py): re-validates schema/caps/OS per call,
  loads only by hash match, spec.json inside the archive must name the same tool/version/id.
- Reads out of the sandbox: `open`/`os.open`/`scandir`/`listdir` resolve via `os.path.realpath`
  (so a symlink into /etc is caught on the read) and are checked against the allowed roots.

### Not covered
- The REAL_CELL tests-gate leg of 9.3a (roadmap says it is not built; `run(path=REAL_CELL)` only
  stamps the report).
- Windows-only code paths (`kill_process_tree`, proactor pipe close) were read but not executed on
  this Linux host.
- CLI surfaces (`cli/tools`, `cli/basket`) were only skimmed; the review focused on the library.

---

<!-- full report: queen -->

## Full report: Queen kernel review — 6 findings

### Summary

The Queen kernel diff (4b66406..HEAD, ~12.8k lines under `packages/hivemind/src/hivemind/queen/`)
adds the Royal Jelly job lane beside the tick (`ticks/tools`), hosting plans that honour
`[llm.floors]` and the measured-grade ledger (`forage/`), the pause-after-checkpoint ordering in
`cluster/protocol`, the shared remote-Warden attach path (`attach/`), and the whole swarm gate
(`swarm_gate/`: reconcile loop, holds, colonization/renewal/Nuc flows, rung decisions, recall of
Handoffs). The risk concentrates in the swarm gate, which runs its own `TaskGroup` of flows and
Level 0 Wardens *concurrently* with the Queen's tick and mutates shared Brood Chamber state from
both sides; its error handling only knows `SwarmError`/`CellError`/`sqlite3.Error`, so an ordinary
chamber conflict escapes and takes the Hive down (F1, reproduced). The second structural gap is
that the Basket's uplink verifier only knows Virtual Cell keys, so a colonized device's Warden can
never ship a blob up (F2). The rest of the diff (forage ledger, hosting floors, fan-out transport,
link reader hand-over, trail signature check, tool lane lifecycle) read as sound. Not shippable as
is: F1 and F2 need a pass; F3 is a real race worth fixing in the same pass.

### Findings

#### F1. A Brood Chamber `InvalidTransitionError` escapes the swarm gate and tears the whole Hive down   [severity: HIGH] [confidence: HIGH]
- File: packages/hivemind/src/hivemind/queen/cluster/protocol.py:266-286 (`_pause_affected`);
  packages/hivemind/src/hivemind/queen/swarm_gate/colonize/steps.py:348-351 (`resume_all`);
  packages/hivemind/src/hivemind/queen/swarm_gate/gate.py:87 (`_GateLoop._recoverable_errors`);
  packages/hivemind/src/hivemind/queen/swarm_gate/reconcile.py:134-147 (`sweep`), :243-252
  (`_retire`); packages/hivemind/src/hivemind/queen/swarm_gate/colonize/flow.py:327 (`run_flow`);
  colonize/promote.py:114; colonize/renew/flow.py:129.
- What: `pause_on_warden` checks `task.status is RUNNING` once, then awaits the HANDOFF intervene,
  `_wait_for_checkpoint` (2000 yields, plus up to `HANDOFF_PATIENCE_S` = 30 s of real time for a
  move/renewal) and only then calls `deps.chamber.pause(task_id)` without re-reading the task.
  `resume_all` calls `resume_paused` for every paused task without checking it is still PAUSED
  (unlike `cluster.resume`, which skips). Both raise `InvalidTransitionError` (a `ConflictError`
  → `HiveMindError`, *not* a `SwarmError`/`CellError`/`sqlite3.Error`) when the task moved
  meanwhile, and nothing in the gate catches that type.
- Path to failure: the gate's flows and `sweep` run concurrently with the Queen's tick. A device
  Warden's bee finishes its command while a colonization/renewal is waiting for its Handoff (or a
  TaskResult lands during a Level 0 rebuild's pause, or the human cancels a PAUSED task while a
  move is in flight); the Queen's tick completes/cancels the task; `chamber.pause`/`chamber.resume`
  raises. In a flow: `Promotion.run`/`Renewal.run` catch only `(SwarmError, CellError,
  sqlite3.Error)`, so `run_flow` re-raises into the gate's `TaskGroup`. In the reconcile path:
  `sweep` catches `(SwarmError, CellError)`, `_GateLoop._recoverable_errors` is the same set, so
  `TickLoop.run` propagates. Either way `SwarmGate.run()`'s `TaskGroup` cancels every device
  Warden's run loop and every other flow (leases not released, `stop()` never ran), and
  `run_hive`'s outer `TaskGroup` then cancels the Queen and the Hive Stand Warden: one task
  finishing at the wrong moment ends the Hive.
- Evidence: `python -c` MRO check: `issubclass(InvalidTransitionError, (SwarmError, CellError,
  sqlite3.Error))` → `False`. Throwaway test at
  .claude/bug-review-2026-10-16/queen/test_repro_pause_race.py.txt
  (GateRig, a Level 0 device with a RUNNING task, terms changed; `_send_handoff_intervene` patched
  to complete the task first, as the Queen's tick would): `await rig.reconciler.sweep()` raises
  `InvalidTransitionError` — passes. The code's own comment at protocol.py:267-270 says "anything
  else is left exactly as it is rather than raising", but only for the pre-wait check.
- Fix sketch: in `_pause_affected`, re-read the task after `_wait_for_checkpoint` and skip (or
  `try/except InvalidTransitionError`) around `chamber.pause`; in `resume_all`, skip tasks whose
  status is no longer PAUSED like `cluster.resume` does; and add `BroodChamberError` (at least
  `InvalidTransitionError`, `TaskNotFoundError`) to the gate loop's, `sweep`'s and the flows'
  recoverable sets so a chamber conflict can never end the gate.

#### F2. A colonized device's Warden can never ship a Basket blob up: the Queen's verifier only knows Virtual Cell keys   [severity: HIGH] [confidence: MEDIUM]
- File: packages/hivemind/src/hivemind/queen/ticks/tools/transfer/receive.py:249-256
  (`_verifier`); packages/hivemind/src/hivemind/queen/cell_gate/provider.py:260-265
  (`uplink_key`); packages/hivemind/src/hivemind/cli/compose/deps/queen.py:154 (the only wiring).
- What: `_verifier` trusts the Hive key plus `deps.uplink_keys(link)`. The only implementation
  wired is `LifecycleVirtualCellProvider.uplink_key`, which answers
  `gate.expected_key(link.cell.id)` from the Virtual Cell readiness gate; a Swarm device's Cell is
  never registered there, so for a device Warden's link the verifier holds the Hive key alone
  (and with `[virtual_cells]` absent, `uplink_keys` is `None`).
- Path to failure: a colonized device's Warden is composed by `cli.in_cell.borrowed`
  (`cli/in_cell/deps.py:286-289`): `node_signer=config.signer`, its own node key. It signs the
  manifest of every blob it ships (`wardens/ticks/basket/door.py`: a `retain` result, or the
  package of a cell-scope tool it scaffolded before asking for a sandbox with `tool.quarantine`).
  The Queen's `TransferReceiver.on_offer` calls `offer.manifest.verify(verifier)` →
  `UnknownSignerError` → `Refuse(UNKNOWN_SIGNER)` → `basket.refused` on the trail. Every upload
  from a colonized device is refused, so phase 9's "a Warden requests a cell-scope tool for its
  machine, gets a sandbox from the Queen" and 9.2a's `retain` from a device never work past
  Level 0. The swarm e2e suite has no Basket case (`grep -rn basket tests/e2e/swarm` is empty),
  which is why nothing caught it.
- Evidence: `receive.py:252-254` only adds a key when `uplink_keys(link)` returns bytes;
  `provider.py:262` reads `self._gate.expected_key(link.cell.id)`; `GateSegmentKeys`/`uplink_key`
  docstrings both say the gate holds keys "from provisioning to teardown" of Virtual Cells only.
  The swarm gate has the device's pinned key (`swarm_gate/keys.py` uses `registry.node_by_node_id`
  for trail segments) but nothing routes it into `uplink_keys`.
- Fix sketch: make `uplink_keys` consult the swarm registry for a link whose `node_id` is an
  enrolled node (the composition root can chain `provider.uplink_key` with a
  `RegistrySegmentKeys`-style lookup by `link.node_id`), and add a swarm e2e that ships a blob up
  from a colonized device.

#### F3. A returning device's Warden can be told to cancel a task the dispatcher just re-placed on it   [severity: MEDIUM] [confidence: MEDIUM]
- File: packages/hivemind/src/hivemind/queen/attach/admit.py:141 (`held.release` at attach);
  packages/hivemind/src/hivemind/queen/swarm_gate/unreachable/devices.py:194-213 (`found`),
  :271-281 (`_replace`); unreachable/effects.py:113-130 (`cancel_abandoned`).
- What: after the grace ends, `_replace` takes the device's tasks back (PENDING) and remembers
  them in `hold.abandoned`. When the Warden dials in again, `attach_warden` releases the
  dispatcher's hold *immediately* (admit.py:141), in the listener's task, while
  `UnreachableDevices.found` (which sends `TaskCancel` for every abandoned task) only runs on the
  gate's next reconcile pass. The abandoned task ids are matched by id only, not by attempt or by
  where the task is now.
- Path to failure: device lost → grace over → task T taken back to PENDING with a resume hint
  (often pinned to that very device by its goal ceiling, so it waits PENDING). Warden returns →
  attach releases the hold → the Queen's tick (woken by the link's first frames) runs
  `dispatch_ready` and places T on the returning Warden (attempt+1, resume inline) → the gate's
  sweep runs `found` → `cancel_abandoned` sends `TaskCancel(T)` → the Warden
  (`wardens/ticks/control.py forward_control`) marks the fresh sub-bee cancelled and relays the
  cancel → the task the Queen just resumed is killed and reported CANCELLED. The two orders are
  racing tasks (tick vs gate loop) with no ordering guarantee.
- Evidence: `test_a_warden_that_returns_after_its_tasks_were_taken_is_told_to_cancel_them`
  calls `found` by hand right after attach, so the race is not exercised; admit.py:141 comment
  "The dispatcher resumes what it was holding" shows the release is deliberate and early.
- Fix sketch: either do not release the hold in `attach_warden` for a Cell the swarm gate holds
  (let `found` release it, after `cancel_abandoned`), or filter `abandoned` in `found` to tasks
  whose chamber row is not currently placed on this Cell (and/or carry the attempt number in
  `TaskCancel` so the Warden ignores a cancel for an older attempt).

#### F4. `Promotion` opens the Cell to the dispatcher before the move is committed; `Renewal` does not   [severity: LOW] [confidence: MEDIUM]
- File: packages/hivemind/src/hivemind/queen/swarm_gate/colonize/promote.py:130-175
  (`_hand_over`, `_commit`); compare colonize/renew/flow.py:153-154.
- What: `pause_for_move` holds the Cell, but the device Warden's attach is a reconnection, which
  releases the hold (admit.py:141). `Renewal._restart` re-holds right after `arrivals.wait`
  ("Its attach was a reconnection, which opened the Cell; nothing is placed until it resumes");
  `Promotion._hand_over` does not, and `take_back_the_rest` already ran before START.
- Path to failure: between the device Warden's attach and `_commit`'s `resume_all` (a registry
  write, a trail write, a `tell_device`, each an await) the Queen's tick can place a PENDING task
  on the device Warden. If `_commit` then fails (`level_not_set`, a trail/sqlite error),
  `_undo` → `dismiss_remote` detaches and closes that Warden, `_restore` rebuilds the Level 0
  Warden under the *same* warden id and resumes only `move.paused`. The freshly placed task stays
  RUNNING in the chamber with a `warden_id` that now names a Warden that never received its
  `TaskAssign`; liveness sees heartbeats, so nothing ever notices.
- Evidence: test_promote.py:210 asserts the Cell is open once the Warden arrived, i.e. before
  `finish()`, so the window is by construction; the Renewal re-hold at renew/flow.py:154 shows the
  authors knew the attach opens the Cell.
- Fix sketch: re-hold the Cell after `arrivals.wait` in `_hand_over` as `Renewal` does (and run
  `take_back_the_rest` again in `_undo` for anything placed on the device during the window).

#### F5. A freshly attached sandbox Cell is caught up on every hive-scope tool   [severity: LOW] [confidence: HIGH]
- File: packages/hivemind/src/hivemind/queen/attach/admit.py:149 (`announce_current_tools` on
  every attach); packages/hivemind/src/hivemind/queen/ticks/tools/announce.py:113-124
  (`_catch_up`, no `sandbox_cells` check; compare `announce` at :87);
  packages/hivemind/src/hivemind/queen/ticks/tools/runner.py:221 (`sandbox_cells.add` only after
  `acquire_sandbox` returned, i.e. after the attach).
- What: the module invariant says "Sandbox Cells are never announced to", and `announce` honours
  it, but the catch-up job started by `attach_warden` for the sandbox's own Warden does not, and
  the sandbox id is not even in `sandbox_cells` yet when that attach happens.
- Path to failure: any Hive with promoted hive-scope tools and `[royal_jelly] sandbox = "virtual"`:
  every Comb run provisions a no-network sandbox whose Warden is immediately shipped every current
  tool package and sent `tool.promoted` for each, installing third-party tools into the Cell that
  is supposed to hold only the package under test, and spending transfers/`tool.announced` rows
  per quarantine. Harmless to the verdict (blobs are inert, the Comb runs its own package) but it
  contradicts the sandbox's isolation story and adds load on every run.
- Fix sketch: skip the catch-up for a link whose Cell carries the `comb-sandbox` purpose label
  (`sandbox_cell_spec` sets it), or have `acquire_sandbox` register the Cell in `sandbox_cells`
  before its Warden can attach.

#### F6. A Level 0 rebuild/revoke pauses bees with no patience, so a bee inside a command loses its progress   [severity: LOW] [confidence: MEDIUM]
- File: packages/hivemind/src/hivemind/queen/swarm_gate/reconcile.py:243-252 (`_retire`,
  `polite=True`); compare colonize/steps.py:318-321 (`HANDOFF_PATIENCE_S` for a move).
- What: `pause_on_warden` without `patience_s` gives a bee only scheduling yields to write its
  Handoff. The protocol docstring explains the patience exists because "a bee inside a command
  writes its Handoff only when the command returns" and "the Warden it ends takes its bees with
  it", which is exactly a rebuild's situation (the Warden is stopped right after), yet `_retire`
  passes none. `take_back` then notes no Handoff and the task resumes fresh on the rebuilt Warden
  ("Each bee writes a Handoff before its Warden goes" at reconcile.py:247 is not true for a bee
  in a long command).
- Path to failure: operator runs `hive swarm access/shield/watch` on a Level 0 device while a
  Drone is in a minute-long command; the pause lands no Handoff; the rebuild takes the task back
  to PENDING with `resume_from=None`; the work is redone from scratch.
- Fix sketch: pass `patience_s=HANDOFF_PATIENCE_S` (or a `[swarm]` knob) to `pause_on_warden` in
  `_retire` when `polite`.

### Looked at and found sound

- `cluster/protocol.py` pause ordering (HANDOFF → wait → TaskPause → chamber.pause) and
  `_newest_handoff`/`recall_handoff` (no double filing: the HANDOFF Nectar's `observed_at` is the
  checkpoint's `written_at`, so the `<=` break holds; taint/cleared ordering, isolation window,
  clearance raise and the Real-Cell-floor exception are all consistent with the tests).
- `cluster/tick.py` skipping `UnknownProviderError` on the health probe.
- `attach/`: `FanoutTransport` (single reader, bounded by a semaphore, end marker never waits),
  `serve_warden` reaping the pump on every exit, `detach_if_current`, `_expect_replay` before the
  pump reads; `LinkReaders.add` carrying a dead link's unread frames to the new queue.
- `forage/`: `evict_source` rule, `resend_state` (unexpired live grants only, unknown sources
  skipped), `_floor_for` capping a floor at the best candidate, `_with_local` replacing by id,
  `resend_plan` refusing a plan naming a gone source, `MeasuredGradeBook` + migration 0003 and the
  SQLite upsert/ISO round-trip, `ticks/sources` host/cell checks and idempotent set_capacity.
- `dispatcher/ready/handoff.py`: `_is_remote` by proved node, tainted/above-clearance → fresh with
  a reason, `HandoffNotFoundError` → reference alone, `fit_in_frame` trial codec sized for the
  signature overhead; `_placed_link` honouring `held`.
- `holds/book.py`, `takeback.py` (status-by-status paging, BLOCKED → withdraw → pause → unassign,
  `InvalidTransitionError` caught per task, grants revoked via ACTIVE copy), `recall.py`.
- `swarm_gate/gate.py` stop ordering (flows, Wardens, listener) and the `_GateLoop` wake/stop
  waits; `wardens.py` `stop`'s cancel-safe re-insert, `Warden.stop` setting the flag first so
  `await served.task` ends; `unreachable/decide.py` table and limits; `hive_stand.py` diagnosis
  task creation/cancel bounded and never raising into the hold; `rungs/` (pure decision, keeper
  back-off, one-move-per-reason, idle re-check before lowering); `nuc/` flows' undo paths and
  `NucDesk.ask` registering before send; `Withdrawal`/`Renewal`/`fall_back_to_level0` ordering.
- `trail/signature.py` + `sync.py` (authenticate before merge, required vs optional keys, no key
  material in errors), `attach/refusals.py` (another-node/another-cell checks, record once),
  `cell_gate/segment_keys.py`, `cell_gate/listener.py` `_GateVerifier` first-frame key resolution.
- `ticks/tools`: job lane lifecycle (`start_job` closes an unstarted coroutine, `stop_tools`
  cancels and awaits, `forget_warden` fails pending orders/shipments), `runner._acquire` shielding
  the provision and releasing on cancel, `_in_cell` release in `finally`, report matched by
  correlation id and Warden, `_checked` requiring tool id/package/spec hashes, `requests._admit`
  (first-hop identity, hive scope on own Cell, capability ceiling from the requester), quarantine
  door checks, `promotions` principal from the link and the kept record, `transfer/ship`
  (one transfer per key, future settled in `finally`), `receive` (provenance from the link, deposit
  before the final ack, spool unlinked), `drain` interval and one job per row.
- `placement`: `fits_comb_shield` ordering and `_tier_fitting_specs` (NIGHT_VEIL never chosen
  outside its path); `planner`/`goal_submission` `survives_link_loss` only ever switched on;
  `autopilot/table` RECORD for replayed `CellHeartbeat`/`CapacityReport`.
- `queen.py`: `stop` order (provisions → tool jobs → readers → planning); `state.py` unchanged.

### Not covered

- `queen/chat/` and `queen/quarantine/` have no changes in this diff (the phase 12 chat/observation
  write path lives outside `queen/`), so they were not reviewed beyond confirming that.
- `cell_gate/listener.py`/`provider.py`/`refusals.py` were read at HEAD for the attach and sandbox
  paths only; the transport limits (`transport_options`, replay window) and
  `cluster/orders.py`/`health.py` were not traced in depth.
- `ticks/tools/review.py` and `honey.py` were read but their LLM/Honey collaborators were not
  followed.
- No e2e tests were run; the one throwaway reproduction is noted under F1.

---

<!-- full report: cli -->

## Full report: CLI and manifest review — 4 findings

### Summary
`hivemind.cli` is the composition root: `cli/compose` wires stores, registry, Fanner, Queen, Hive
Stand Warden, Virtual side, Royal Jelly and the Swarm into one `Hive`; `cli/in_cell` is the same
root for a Warden that runs inside a Virtual Cell or on a colonized (borrowed) device; `cli/tools`,
`cli/basket`, `cli/swarm` and `cli/llm` are operator commands over the same stores. `manifest/`
adds the phase 8/9/11 sections and the device manifest. The wiring is careful: no `subprocess` in
`cli/` or `manifest/`, every listener-shaped knob (`[swarm] listen_host`, `advertise_url`) refuses
non-loopback without TLS, secrets stay in `SecretStr`/files and never reach a log or an error
message, and every shutdown path I traced closes what it opened. The risk concentrates at the
seam between the Queen's colonization decision and the borrowed Warden's own start-up checks
(F1), and in a device manifest table that is validated and then dropped (F2). Shippable after F1
is resolved on one side or the other; F2–F4 are small.

### Findings

#### F1. A PROPOLIS device can never be colonized: the borrowed Warden refuses the tier the packet hands it, and nothing upstream refuses the move   [severity: HIGH] [confidence: HIGH]
- File: packages/hivemind/src/hivemind/cli/in_cell/borrowed/config.py:192 and :269-276 (`_check_tier`); packages/pollen/src/pollen/bootstrap/warden.py:124; packages/hivemind/src/hivemind/swarm/level.py:254-263 (`_cannot_carry_a_warden`)
- What: `build_borrowed_config` calls `_check_tier(base.spawn_config.comb_shield)`, which raises `ConfigurationError` for anything but MEADOW ("A Swarm device's Warden holds the MEADOW tier only"). The Pollen Packet starts the borrowed Warden with `HIVEMIND_COMB_SHIELD = state.comb_shield.value`, i.e. the node's current tier, which the operator can set to PROPOLIS (`hive swarm invite --shield propolis`, `hive swarm shield <node> propolis`; ADR-0051, roadmap 11.12). The Queen's colonization decision (`decide_colonize` → `_cannot_carry_a_warden`: only level, Python version, access level and Forage) and the colonize flow never look at the tier, so a PROPOLIS node is a legal colonization target.
- Path to failure: operator enrols a VPN'd device with `--shield propolis` (or re-shields it later); operator runs `hive swarm promote <node>` (or a `--survives-link-loss` goal lands on it, or relief pressure trips) → `desired_level = COLONIZED` → the Queen PREPAREs and STARTs the Warden on the device → `pollen` execs `hive in-cell` with `HIVEMIND_CELL_LEASE=borrowed` and `HIVEMIND_COMB_SHIELD=propolis` → `run_in_cell_warden` → `run_borrowed_warden` → `build_borrowed_config` → `_check_tier` raises before anything connects → the process exits → the START step fails (`ColonizeStepError("start_failed")`) → the flow rolls the rung back and `RungKeeper.failed` schedules a retry (`retry_after_s · 2^min(n-1,5)`); `desired_level` is still set, so the next pass asks `decide_colonize` again, which still says yes → the Hive retries the doomed move forever (every ≤32·retry_after_s), each attempt checkpointing and resuming the device's running tasks. `hive swarm promote` meanwhile printed "asked for COLONIZED ... it moves the device's Warden on its next beat" (cli/swarm/changes.py:165) and nothing ever tells the operator why it does not. `test_a_device_holds_the_meadow_tier_only` (tests/unit/cli/in_cell/borrowed/test_config.py:175) pins the refusal, and the Propolis e2e (`tests/e2e/test_phase11_exit_propolis.py`) only proves PROPOLIS at Level 0, so no test sees the two halves together.
- Evidence:
  ```python
  # borrowed/config.py:269
  def _check_tier(tier: CombShieldLevel) -> None:
      if tier is not CombShieldLevel.MEADOW:
          raise ConfigurationError(f"A Swarm device's Warden holds the MEADOW tier only, not {tier.value}: ...")
  # pollen/bootstrap/warden.py:124
  "HIVEMIND_COMB_SHIELD": state.comb_shield.value,
  # swarm/level.py:254  (no tier check anywhere in the refusal ladder)
  if facts.level is not RuntimeLevel.GATEWAY: return "not_gateway"
  ... python ... access ... forage
  ```
- Fix sketch: pick a side. Either `decide_colonize` refuses `comb_shield is PROPOLIS` with its own word (and `hive swarm promote` reports that refusal instead of "asked for"), or the borrowed Warden accepts PROPOLIS and attests the tunnel itself the way a Propolis Virtual Cell already does (`cli/in_cell/propolis.own_tunnel_attestation` on its `CellReady`), with `_check_tier` reduced to refusing NIGHT_VEIL.

#### F2. A device manifest's `[llm.floors]` is validated and then silently dropped; the borrowed Warden routes on the shipped default floors   [severity: MEDIUM] [confidence: HIGH]
- File: packages/hivemind/src/hivemind/cli/in_cell/borrowed/llm.py:80-115 (`device_llm`, `_slot_bindings`); packages/hivemind/src/hivemind/cli/in_cell/deps.py:249-271 (`_routed`); packages/hivemind/src/hivemind/manifest/device.py:162 (`llm: LlmSection`)
- What: `DeviceManifest.llm` is the full `LlmSection`, so `[llm.floors]` is accepted and checked (`reject_unknown_floor_keys`, grade bounds). `device_llm` converts providers, seats, limits, slots and servers but never reads `section.floors`, and `build_core_deps` → `_routed` builds `RoutingRules(offline=config.llm_offline)` with no floors, so a colonized device's Warden routes every grant on `default_floors`. The `_routed` docstring justifies this for a Virtual Cell ("carries no `[llm.floors]` of its own") but the same code now serves a device that does.
- Path to failure: an operator copies the Hive Manifest's `[llm]` into the device manifest (which is what `docs/manifests/device.toml` and the section docstring invite: "the Hive Manifest's own `[llm]` shape"), including `[llm.floors] worker = 4`. The file validates. On the device, `choose_binding` sees floor 2 for WORKER and routes a task to a grade-2/3 local model the operator meant to exclude; on the Hive Stand the same manifest text would have refused that model. No error, no log line.
- Evidence: `device_llm` returns `DeviceLlm(providers, seats, limits, slots, servers, hostable, offline)`; `InCellRuntimeConfig` has no floors field; `_routed`: `routing_rules=RoutingRules(offline=config.llm_offline)` (deps.py:265). `grep floors packages/hivemind/src/hivemind/cli/in_cell/` finds only docstrings.
- Fix sketch: carry `section.grade_floors()` through `DeviceLlm` into `InCellRuntimeConfig` (or a `RoutingRules` field on `BorrowedConfig`) and build `RoutingRules(floors=..., offline=...)` for the borrowed Warden; or, if a device is meant to inherit the Hive's floors, have `DeviceWatchSection`-style `mode="before"` validation refuse `floors` in a device manifest with a reason, as `roots` already is.

#### F3. `run_in_cell_warden` leaks a connected Queen link when announcing or building deps fails after `transport.connect()`   [severity: LOW] [confidence: HIGH]
- File: packages/hivemind/src/hivemind/cli/in_cell/main.py:141-146 and :209-274
- What: `_connect_and_build` (and inside it `_connect_and_announce`) connects the transport at `cell_link.announce` (main.py:268), then sends `CapacityReport` and `CellHeartbeat` and builds the Warden deps; `Warden(config.warden_id, deps)` is built at :142; only then does the `try/finally` that closes the link begin at :146. Any exception or cancellation between :268 and :146 (a dropped socket during `send_capacity_report`, a provider table that fails in `build_in_cell_warden_deps`, the backend cancelling the process while it announces) leaves the connected transport open, against the module's own key invariant "once the link is built it is closed on every way out of `run_in_cell_warden`". For a Night Veil Cell the same gap exists around `redialing.equip(deps)` after `connect_redialing` has brought the mending link online.
- Path to failure: the Queen's `CellListener` closes the socket between the Cell's `CellReady` and its `CapacityReport` (listener restart, Hive shutdown racing a provision) → `send_capacity_report` raises `TransportError` → `run_in_cell_warden` propagates with the transport's reader task still alive and the socket open. In the ENTRYPOINT the process exits and the loop cancels it, so the practical effect is limited to the in-process callers (the e2e harness, `tests/builders`) and to the invariant being false; `test_run_in_cell_warden_finishes_its_cancellation` only cancels after the three frames were received, so the gap is untested.
- Evidence: `transport, deps = await _connect_and_build(config, clock)` (:141) and `warden = Warden(...)` (:142) sit before `try:` (:146); `_shut_down`/`_close_link` run only from that `finally`.
- Fix sketch: wrap `_connect_and_build` so that a failure after `connect()` closes the transport (a `try/except BaseException: await _close_link(transport); raise` inside `_connect_and_announce` after `announce`, and the same around `equip` for the redialing link), or move the `try` up to cover the build.

#### F4. The borrowed Warden's SQLite stores and outbox are opened outside the block that tears the Warden down, and the outbox is opened with blocking I/O on the loop   [severity: LOW] [confidence: MEDIUM]
- File: packages/hivemind/src/hivemind/cli/in_cell/borrowed/run.py:107 and :126-130; packages/hivemind/src/hivemind/cli/in_cell/borrowed/deps.py:144
- What: `run_borrowed_warden` opens three connections to `warden.sqlite` (`open_device_stores`, :107) and `_serve` builds the link, which opens the durable outbox file (`open_outbox`, deps.py:144, a synchronous read of the whole file on the event loop; the Night Veil twin in `cli/in_cell/redial/build.py:117` wraps the same call in `asyncio.to_thread`), before the `try:` at :130. Nothing closes the stores on any path (`_close` closes only the link). A `build_device_link` failure (a corrupt outbox → `OutboxError`) or a `hive_stand_addresses` failure leaves them open.
- Path to failure: the process is the unit of lifetime here (the packet restarts it), so the leak is bounded by process exit; the realistic cost is a WAL file left with an open writer when the Warden dies by exception and the packet restarts it immediately, plus a loop stall proportional to the outbox size on a device that spent a long outage queuing frames.
- Evidence: `stores = await open_device_stores(...)` has no matching close; `DeviceStores` has no close method; `open_outbox(config.state_dir, codec, clock)` is called from the synchronous `build_device_link`.
- Fix sketch: open the outbox through `asyncio.to_thread` as the redial package does, and give `DeviceStores` a `close()` called from `_serve`'s `finally` after `_close(link)`.

### Looked at and found sound
- `cli/compose/hive/run.py`: start order (attach, Virtual side, Swarm gate, lease) and the new `_unwind` on a failed `_start`; shutdown order (ripening, swarm gate → every device Warden releases its lease while the Queen still runs, Queen, Warden, links, registry). `SwarmGate.stop` and `CellListener.stop` are safe on a gate/listener that never bound.
- `cli/compose/deps/*`: one `ProviderRegistry`, one `Fanner`, one `Enforcer`, one `RoyalJellyAccess` shared by Queen and Hive Stand Warden; `_hosting` (model servers, provider door) only on the Hive Stand's Warden; device Wardens get their own `LeaseScopedGuard`/`SwarmSource`/links and the Hive's shared stores. `build_hive_links` keeps a Swarm node's enrolment-minted `warden_id`.
- `cli/compose/swarm/*` and `manifest/schema/swarm.py`: the listener refuses a non-loopback `listen_host` without a cert/key pair, `advertise_url` goes through `check_waggle_uri` (ws:// only on loopback); the Hive key is read from the secret store only, never over Waggle; `LeaseScopedGuard` refuses a command on any lease but the one it was bound to.
- `cli/compose/royal_jelly/*`: subprocess sandbox logged as dev-only; `_virtual_sandbox_needs_a_backend` keys on `model_fields_set` and `apply_env._revalidated` dumps with `exclude_unset=True`, so the env-override round trip keeps that answer.
- `cli/tools/submit.py`: the `_ReplyTap` link captures the Queen's correlated `tool.promoted`/`control.error` (the job keeps the tap as its `link`), forwards the promotion to the real Warden, times out on `[royal_jelly] request_timeout_s`, and `build_hive` runs in a worker thread because it calls `asyncio.run` itself (sqlite connections are `check_same_thread=False` behind a `ConnectionThread`).
- `cli/tools/quarantine.py`: packs a directory deterministically, skips `__pycache__`, treats bad JSON/unknown limit keys as exit 2; the Comb's `SandboxSubprocess` is cleaned up in `finally`.
- `cli/basket/*`: deposit as the Hive Stand with `from_borrowed_cell=True`; ship refuses retired/tainted blobs; evict/retire record trail events as `human` only when something changed; prefix resolution refuses ambiguity.
- `cli/swarm/*`: `run` refuses when the Swarm is off or the node is revoked before building a Hive, pins the goal with `cell:real:<cell>` as the first capability, deducts the reconnect wait from the timeout, and waits for the lease to be released; `watch` validates absolute paths in the device's own spelling (`absolute_device_path`, ntpath vs posixpath chosen by the text); `invite` prints the string on stdout alone.
- `cli/in_cell/borrowed/*` otherwise: ids/keys are the packet's, the node seed is never echoed, `wss://` requires a valid pin and `ws://` forbids one, the strict URI rule is re-applied over the Virtual-Cell carve-out, `HIVEMIND_WATCH_ROOTS` is parsed as JSON against the wire's `WatchRoots` bounds and reported back unchanged, signals set `stop` via `call_soon_threadsafe` and handlers are restored. `_run_until_stopped`/`_settle` bring the Warden to STOPPED on every exit and re-raise what `run()` raised; the link is closed in `finally`.
- `cli/in_cell/redial/*`: `ProxyDialer` refuses to exist without a SOCKS proxy; `AfterRedial` skips the first connection; the outbox lives under the scratch root and is opened off-loop.
- `cli/llm/local.py` and `cli/llm/eval/*`: manager always `aclose()`d; grade bands `min(5, 1 + floor(score·5))` match `BAND_LINES`; `_default_models` in `cli/stores.py` keeps slot-first, table-order precedence over `[forage.map]` rows; `build_forage_map` ignores measured grades for ids no longer on the map; `--update-map` writes only when there are entries.
- `manifest/env/*`: the split is a pure move; `HIVEMIND_LLM_OFFLINE` semantics unchanged; `HIVEMIND_ENTRANCE_TUNNEL_` with an empty name refused; `provider_api_key` reads only `environ`.
- `manifest/schema/llm/{floors,servers,section}.py`, `supervision.py`, `royal_jelly.py`, `device.py`: floors keyed only by real slots; servers need an `openai_compat` loopback provider on their own port and map rows of their own provider; reconnect budget strictly inside `max_offline_s` on both Hive and device; `[supervision.watch] roots` absolute in either spelling and NUL-free; device manifest refuses `roots` with a reason and refuses map rows no server lists or that name a host Cell.
- `cli/readback/cells.py`: Swarm rows are read from the registry and trail only; revoked devices skipped; the mode edge is the newest `warden.*` event.
- No `subprocess` import anywhere under `cli/` or `manifest/`; `os.environ` is read only at command entry points and passed down.

### Not covered
- `cli/landing/`, `cli/entrance/`, `cli/keys/`, `cli/remote/`, `cli/recordings.py`, `cli/serve.py`: unchanged since 4b66406 (not in the diff), so not re-reviewed.
- `cli/basket/render.py`, `cli/tools/render.py`, `cli/swarm/render.py` beyond a skim (formatting only).
- The Queen-side colonization flow and `pollen/bootstrap` were read only as far as F1 needed; `cli/llm/eval/goals.py`, `transcriber.py` and `cli/compose/entrance.py`'s Honey browser/image intake were skimmed, not traced into `entrance/`.

---

<!-- full report: llm -->

## Full report: LLM layer, Observation Hive (Python) and small subsystems review — 8 findings

### Summary
The phase 8 LLM work adds a pure routing policy over a slot's fallback chain, per-call pricing in
the Fanner lane, character-based usage estimates for servers that report none, prompt overlays at
the provider door and an Ollama management client; phase 12 adds pure read-model builders under
`observation/api`, `observation/metrics` and the Honey browser views, with the Entrance gathering
the records. The pure builders are careful (frozen models, no store access, every list cut by the
viewer's reader) and the 2,337 unit tests under `tests/unit/{llm,observation,memory,guard,common}`
pass. The risk concentrates in the money and grade arithmetic that phase 8 introduced: the new
pricing never bills Anthropic cache reads (so spend caps are under-enforced on exactly the cached
Queen prompts the stable-prefix design exists for), and routing's capped grade floor disagrees
both with itself (it caps at the whole map, not the chain) and with the Fanner's uncapped spill
rule. The Honey browser has one taint-probing leak. Shippable after a pass on F1 to F4; the rest
are small.

### Findings

#### F1. Anthropic cached input tokens are never priced; cache-creation tokens are dropped, so spend is under-counted and spend caps under-enforced   [severity: HIGH] [confidence: MEDIUM-HIGH]
- File: packages/hivemind/src/hivemind/llm/fanner/pricing.py:88-92; packages/hivemind/src/hivemind/llm/providers/anthropic/mapping.py:455-462; packages/hivemind/src/hivemind/llm/models.py:261-263
- What: `price_usage` bills `input_tokens * in_price + output_tokens * out_price` and never looks at `cached_tokens` (the comment says "cached tokens are billed as input here", which is only true if `cached_tokens` is a subset of `input_tokens`, as `Usage.cached_tokens`'s docstring promises). The Anthropic adapter breaks that contract: it maps `input_tokens=usage.input_tokens` (which in the Anthropic API excludes cache reads and cache writes) and `cached_tokens=cache_read_input_tokens`, and discards `cache_creation_input_tokens` entirely. The OpenAI-compatible adapter's `prompt_tokens` does include its `cached_tokens`, so the two adapters put different meanings behind the same field and the one pricing function cannot be right for both.
- Path to failure: the Queen's awake episode on Claude with a stable (cached) prefix of, say, 8,000 tokens and 500 fresh tokens. Anthropic reports `input_tokens=500, cache_read_input_tokens=8000`. `price_usage` charges 500 input tokens; the 8,000 cache-read tokens (billed at 10% of the input price) and any cache-write tokens (125%) cost nothing on the trail. `LedgerRecorder.record` (queen/forage/ledger/recorder.py:74-79) forwards that `usage.cost_usd` to `ForageLedger.record_spend`, so the goal's `spend_cap_per_goal_usd` is enforced against a figure that omits most of the real bill; `GET /v1/cost` and `hive_llm_cost_usd_total` show the same understatement. Before this diff nothing priced calls at all, so this is new wrong accounting, not a carried-over one.
- Evidence:
  ```python
  token_cost = (
      usage.input_tokens * bound.cost_per_million_input_usd
      + usage.output_tokens * bound.cost_per_million_output_usd
  ) / TOKENS_PER_MILLION
  ```
  and in the Anthropic mapping `input_tokens=usage.input_tokens, cached_tokens=usage.cache_read_input_tokens or 0` with no `cache_creation_input_tokens` anywhere.
- Fix sketch: make the Anthropic mapping honour `Usage`'s contract (`input_tokens = input + cache_read + cache_creation`, `cached_tokens = cache_read`), or give `ModelCost` a cached-input rate and bill `cached_tokens` separately; either way add a unit test that prices an Anthropic usage with cache reads.

#### F2. Routing's grade floor is capped at the strongest source on the whole map, not in the slot's chain, so an unrelated strong source makes a raised-bar task fall back to the manifest primary and ignore Forage   [severity: MEDIUM] [confidence: HIGH]
- File: packages/hivemind/src/hivemind/llm/routing/eligibility.py:288-292; packages/hivemind/src/hivemind/llm/routing/policy.py:96-110
- What: `effective_floor` caps the wanted floor at `max(source.effective_grade for source in inputs.sources)` over every map source, but the chain can only ever choose its own rows. When the Queen's grade-5 model is on the map (every real Hive) and a Worker chain tops out at grade 3, a CRITICAL task (floor 4) finds no eligible row and `choose_binding` "falls back": it returns the manifest primary in manifest order, skipping seat availability, cost, locality and the hosting plan, and with `forage_shortfall=None` so no `ForageRequest` is filed even when that primary has no seat.
- Path to failure: chain `worker` (grade 3, `seats_free=0`) -> `b` (grade 3, seats free). With only those two on the map, CRITICAL routes to `b` (floor capped at 3). Add the off-chain grade-5 `anthropic/opus` and the same inputs route to `worker`, `fell_back=True`, `floor=4`, shortfall `None`. Run: `.claude/bug-review-2026-10-16/llm/probe_cap.py.txt` prints
  ```
  without off-chain source: key=b fell_back=False floor=3 shortfall=None
  with off-chain grade-5:  key=worker fell_back=True floor=4 shortfall=None passed=[('worker', 'GRADE_BELOW_FLOOR'), ('b', 'GRADE_BELOW_FLOOR')]
  ```
  The same cap drives `route_rebind` (wardens/spawn/route.py) through `reason.floor`, so a rebind escalation on such a chain also lands on the primary.
- Evidence: `return min(wanted, max(source.effective_grade for source in inputs.sources))` with `inputs.sources` being the whole map; the docstring's stated intent ("so the best available qualifies") is only met when the best available is in the chain.
- Fix sketch: cap at the best effective grade among the sources the chain's rows actually name (fall back to the map-wide cap only when none is on the map), and when `fell_back` still rank the ineligible rows by the same key rather than manifest order.

#### F3. The Fanner's spill rule uses the uncapped tempo floor, so on a Hive whose best model is below that floor every CRITICAL call spills past routing's choice to the weakest link   [severity: MEDIUM] [confidence: HIGH]
- File: packages/hivemind/src/hivemind/llm/fanner/spill.py:89; packages/hivemind/src/hivemind/llm/fanner/lane.py:287-293
- What: routing caps the floor at what the map holds (eligibility.py:292) and reorders the chain accordingly; `static_spill_reason` then compares `source.effective_grade < grade_floor(tempo.accuracy)` with no cap, and `FannerLane.complete` spills whenever that is true and a fallback exists.
- Path to failure: an offline Hive (docs/manifests/offline.toml style) with chain `local_big` (grade 3) -> `local_small` (grade 2), CRITICAL tempo (floor 4). Routing picks `local_big` (capped floor 3) and binds `[local_big, local_small]`. At call time `local_big` is 3 < 4 -> `GRADE_BELOW_FLOOR` spill to `local_small`; `local_small` is 2 < 4 but has no fallback, so the call proceeds there. The task that "must not be wrong" is served by the weakest model on the Hive, with an `llm.spill` recorded as if that were intended; the same happens for HIGH (floor 3) on a Hive of grade-2 models.
- Evidence: `if source.effective_grade < grade_floor(tempo.accuracy): return SpillReason.GRADE_BELOW_FLOOR` and in the lane `if static_reason is not None and current.fallback is not None: ... current = current.fallback; continue`.
- Fix sketch: make the grade spill relative (spill only when the fallback's effective grade is higher than the current's, or when the current is below the floor routing actually applied), or carry routing's capped floor on the lane/BoundModel and compare against that.

#### F4. A tainted Bee Bread entry opened by id through the Honey browser answers 403 `hivemind.memory.tainted` naming the tainting trail event, not 404, so taint status can be probed and the "missing and forbidden look alike" rule is broken   [severity: MEDIUM] [confidence: HIGH]
- File: packages/hivemind/src/hivemind/cli/honey/sources.py:117-123; packages/hivemind/src/hivemind/honey_store/browse/documents.py:290-293 and 315-324; packages/hivemind/src/hivemind/entrance/reads/honey.py:224-249; packages/hivemind/src/hivemind/entrance/gate/handlers.py:143-165
- What: `find_document` (new in 12.9) resolves any non-`honey_`/`wax_` id to `/bee-bread/<id>` and `_read_bee_bread` calls `MemoryBeeBreadSource.entry`, which catches only `BeeBreadEntryNotFoundError` and `ClearanceError`. Both memory stores raise `TaintedMemoryError` (a `PermissionDeniedError`) for a TAINTED entry looked up by id (memory/store/sqlite/store.py:308-310, memory/store/memory.py:398-399). `locate_item` catches only `BrowseNotFoundError`, so the error reaches the Entrance's generic handler, which maps `PermissionDeniedError` to 403 and puts `str(error)` in `detail`. Honey rows and wax notes in the same state are a 404 (`honey_visible` drops tainted rows), so Bee Bread alone leaks.
- Path to failure: a device holding `observe` requests `GET /v1/honey/items/event_01...` for a Bee Bread entry (its id is a trail event id, visible on the trail stream) that quarantine labelled TAINTED. Response: 403 `{"error": "hivemind.memory.tainted", "detail": "Memory item event_01... is tainted (by trail event event_01...) and is refused until a judge verdict ..."}`. An entry that does not exist answers 404. Reproduced at the source seam with `.claude/bug-review-2026-10-16/llm/probe_taint.py.txt`: `RAISED TaintedMemoryError -> Memory item ... is tainted (by trail event ...)`.
- Evidence: `except (BeeBreadEntryNotFoundError, ClearanceError): return None` in `MemoryBeeBreadSource.entry`; `error_body` builds `ErrorBody(error=error.code, detail=str(error))` for every non-500 `HiveMindError`.
- Fix sketch: catch `TaintedMemoryError` in `MemoryBeeBreadSource.entry` (return None, like the other two), and have `bee_bread_visible` also refuse an entry whose `tainted` marker refuses, mirroring `honey_visible`.

#### F5. `OverlayingProvider` does not forward `list_models`, so `hive llm models` refuses any non-Ollama local provider that names a `prompt_overlay`   [severity: MEDIUM] [confidence: HIGH]
- File: packages/hivemind/src/hivemind/llm/registry/overlay.py:208-255; packages/hivemind/src/hivemind/llm/registry/provider_registry.py:178-181; packages/hivemind/src/hivemind/cli/llm/local.py:232-242
- What: the registry wraps a chat provider in `OverlayingProvider` when its row sets `prompt_overlay`; the wrapper forwards `name`, `capabilities`, `complete`, `stream`, `count_tokens`, `health` and `aclose` only. `OpenAICompatProvider.list_models` (added in this diff for the generic listing probe) is therefore hidden, and `_list_rows` checks `isinstance(provider, ModelListing)` structurally and raises `_CommandError("provider ... cannot list models")`.
- Path to failure: docs/manifests/full.toml's `[llm.providers.local]` sets `prompt_overlay = "small_local"` (the documented way to run a small model). Point its `base_url` at vLLM or llama.cpp (not Ollama, so `OllamaManager.detect` is False) and run `hive llm models local`: the command refuses with "cannot list models" although the server lists fine without the overlay. `isinstance(provider, OpenAICompatProvider)`-style checks elsewhere would fail the same way.
- Evidence: `if not isinstance(provider, ModelListing): raise _CommandError(f"provider {target.name!r} cannot list models.", USAGE_EXIT_CODE)` and no `list_models` in `OverlayingProvider`.
- Fix sketch: forward `list_models` (and any other optional, capability-style method the CLI probes for) through the wrapper, or have the CLI unwrap to the inner provider for listing.

#### F6. `OllamaManager.detect` and `open` raise raw `httpx` errors for a `base_url` without a scheme (and for other transport errors outside the mapped set), contrary to their never-raises contract; `open` then leaks its client   [severity: LOW] [confidence: HIGH]
- File: packages/hivemind/src/hivemind/llm/providers/ollama/client.py:284-292 and 391-403; packages/hivemind/src/hivemind/llm/providers/ollama/manage.py:117-133
- What: `_CONNECTION_EXCEPTIONS` lists seven httpx classes; `httpx.UnsupportedProtocol`, `httpx.ProxyError`, `httpx.PoolTimeout`, `httpx.WriteTimeout`, `httpx.LocalProtocolError` and `httpx.InvalidURL` are not mapped, so `is_ollama` (which catches only `LLMError` and `ValidationError`) lets them through. `ProviderSpec.base_url` is a plain `str` with no scheme check.
- Path to failure: `base_url = "localhost:11434/v1"` in the manifest, then `hive llm providers` (health column) or `hive llm models`. `OllamaManager.detect(...)` raises `httpx.UnsupportedProtocol: Request URL has an unsupported protocol 'localhost://'` instead of returning False (reproduced: `.claude/bug-review-2026-10-16/llm/probe_ollama.py.txt`). `open` has no `finally`, so its `httpx.AsyncClient` is never closed on that path.
- Fix sketch: catch `httpx.HTTPError` (the common base) in `_send`/`stream_ndjson`, map the rest to `ProviderUnavailableError`/`ProviderRequestError`, and close the client in `open` on any failure.

#### F7. The Cost view counts amortised local spend as "hosted" and reports it as what going local would save   [severity: LOW] [confidence: HIGH]
- File: packages/hivemind/src/hivemind/observation/api/cost.py:195-204 and 255-263
- What: `_hosted_by_slot` treats every call with `usage.cost_usd > 0` as hosted spend and `SlotSaving.if_local_saving_usd` equals it. Roadmap 8.8 (this same merge) prices a local source's seat time through `cost_per_seat_hour_usd`, so a local call now costs more than zero, and the module's premise ("a call that cost nothing (a local model, or an unpriced one) is not hosted spend") no longer holds.
- Path to failure: an offline Hive whose local source sets `cost_per_seat_hour_usd` (docs/manifests/offline.toml:122 documents exactly that). Every call is local, yet `GET /v1/cost` reports `hosted_usd` equal to the whole spend and an `if_local_saving_usd` of the same amount for a slot that is already local; the front end's slot gauge ("hosted calls out of all calls") shows 100% hosted.
- Fix sketch: decide hosted-ness from the trail's `provider` against the local providers (as routing's `local_providers` does) or carry a `local` flag on the `llm.call` payload next to `priced`/`estimated`, rather than inferring it from a non-zero cost.

#### F8. A streamed reply that carries `usage` on the same chunk as `finish_reason` has that usage ignored and replaced by a character estimate flagged `estimated=True`   [severity: LOW] [confidence: MEDIUM]
- File: packages/hivemind/src/hivemind/llm/providers/openai_compat/mapping.py:455-490
- What: `StreamState.absorb` reads `usage` only from a chunk with empty `choices`; a chunk that has a choice is processed for text, tool pieces and `finish_reason` and its `usage` key is never looked at. `finalize` then (new in this diff) emits an estimated usage. Servers that put the final usage on the finish chunk rather than a trailing usage-only chunk (older llama.cpp builds and some proxies do; the exact set is why confidence is MEDIUM) therefore get token counts guessed from characters even though they reported real ones, and the trail marks the call `estimated`.
- Fix sketch: in the non-empty-`choices` branch, if the chunk carries a usable `usage`, keep it as the pending usage and let `finalize` prefer it over the estimate.

### Looked at and found sound
- `common.tasks.reap`: the `cancelling() > asked` fix is right for the finally-cleanup case; the pre-count can only be stale if a task cancels itself synchronously, which nothing here does.
- `llm/fanner/lane.py`: pricing happens once per completed call on both the non-stream path and the recorded payload; the seat is released on every exit of `_call`; `estimated`/`priced` reach `LlmUsage` with safe defaults for older payloads.
- `llm/fanner/pricing.py`: the None-vs-0.0 distinction, provider-reported cost kept as is, seat-hour amortisation; `Usage.__add__` keeps `cost_usd=None` when either side is unpriced and ORs `estimated`.
- `openai_compat/estimate.py` and `_usage_from_wire`: a `usage` with neither count is "none"; estimates never carry a cost; `count_tokens` keeps its own margin.
- `llm/routing/{rules,effort,inputs,decision}.py` and the rank key: `order` is a permutation of the chain, offline refuses non-local providers before anything else, `is_free` treats a local seat-hour as not-spend, `_source_for` prefers the bee's own Cell's copy.
- `llm/registry`: `add_provider`/`remove_provider` drop a built instance without closing it, but the only caller (`wardens/local_pool/hosting/launch.py:206-207`) closes the provider itself right after a successful unregister; the registry's `aclose` snapshot-and-clear is re-entrancy safe; the transcriber's new `aclose` is found structurally.
- `llm/prompts/overlay.py`: overlay names are confined by `_NAME_PATTERN` before touching the filesystem, the keyed suffix lands after the shipped body (consistent with `render`'s `rstrip("\n")`), a stray `.md` fails loudly.
- `forage`: `effective_grade` is read everywhere a strength is compared; `add_source`/`remove_source` run under the map lock; `Ceilings.scratch_quota_bytes` maps the wire's 0 to None.
- `observation/api/{thoughts,capping,alarms,attendant,cell_page,fleet,summary,hosting}.py` and `observation/metrics`: all pure, frozen, no store access, no text fields (alarms, wax, inbox entries carry ids and enums only); cursors refuse what they did not make; Prometheus escaping of label values and help text; counts bounded.
- `observation/views/honey` and `honey_store/browse`: every listing and document goes through the viewer's `HoneyReader` (scopes plus ceiling); `find` resolves to a path and re-applies `read_document`'s rules; the privileged `locate` read returns only scope and label; search never reveals withheld counts; `/v1/honey/*` are all GETs, the only write path stays the Queen's chat route (nothing under `observation/` imports a store).
- `honey_store/store/sqlite/nectar.py` `select_handoff_nectars`: `origin_tier` is NOT NULL in the schema, so the `<> NIGHT_VEIL` clause cannot silently drop rows; ephemeral and tainted rows are excluded.
- `memory` retention: the in-memory and SQLite `list_bee_bread_of_kind`/`purge_bee_bread_before` touch one kind only, filter TAINTED on read, and the `json_each` ref-id narrowing is parameterised.
- `pheromone`: new kinds in every family are catalogued in `guard/policy/catalogue.py` (tests cover the catalogue); `LlmUsage` gains `estimated`/`priced` with backwards-compatible defaults; skeleton keeps `comb_shield` on `cell.attested`.
- `exoskeleton`: the Chromium poll never sleeps past the deadline; the Openbox config binds `Client` only; `InMemoryRecordingStore` prunes only recordings with no action inside the window.
- `supervision`/`brood_chamber`: `PATROL_ANOMALY` has a policy row; PAUSED -> PENDING is added to `TRANSITIONS` alongside `unassign`'s new docstring.

### Not covered
- `tests/evals/` graders and `cli/llm/eval` (`--update-map` measured-grade writing) were only skimmed for how `measured_grade` reaches `build_forage_map`; the grading arithmetic itself was not reviewed.
- `llm/providers/ollama/fake.py` (test double) and the whisper/sentence-transformers adapters (unchanged in the diff).
- The Entrance routes and streams that call the Observation builders are another reviewer's; I followed them only as far as `entrance/reads/{honey,screens/thoughts,screens/cost}.py` and the error handler needed for F4.
- `wardens/spawn/route.py` and `wardens/local_pool/hosting` were read as callers of routing and the registry, not reviewed on their own.

---

<!-- full report: web_shell -->

## Full report: Observation Hive shell (client, store, share, PWA, Android) review — 5 findings

### Summary
The non-view front end is a typed Landing Board client (signing, session, sockets, push), a
small reducer store the views subscribe to, the shell/router, the Android share/push/invite
host, and the PWA worker. `pnpm generate:check`, `pnpm typecheck` and `pnpm test` (888 tests)
all pass at HEAD, and the client/server contract (field names, signed-string construction over
the raw path and query, close codes, the chat `after` cursor, `PushNotice` kinds) agrees. The risk
concentrates in session-loss handling: the HTTP wrapper treats every 401 on a signed request as
"the session is gone", which is wrong for the step-up routes, and the socket wrapper never ends a
session on a 4401, so the app can either throw a live session away or sit on a dead one. The
Android push path also loses UnifiedPush endpoint rotations. Shippable after a pass on F1 to F4;
F5 is a polish item.

### Findings

#### F1. A mistyped step-up password logs the device out and orphans the server session   [severity: HIGH] [confidence: HIGH]
- File: packages/observation-web/src/landing_board/client/http.ts:160 (with session.ts:171-183 and index.ts:70-73)
- What: `send()` calls `deps.onUnauthorized()` on any 401 answered to a signed request. The
  step-up routes are signed, and the Entrance answers a wrong step-up password with 401
  `hivemind.entrance.authentication_failed` while the session stays alive, so the client deletes
  its key and token and the shell drops to the login screen with "Your session ended".
- Path to failure: logged in, `needs_step_up` (new network) or any `403 step_up_required` action
  -> the chat/devices prompt asks for the password -> person makes a typo ->
  `POST /v1/auth/step-up` -> server `ceremony.py:133-137` `refuse_password` ->
  `AuthenticationFailedError` -> `gate/handlers.py:69` maps it to 401 (the session is not ended;
  only the failure counter moves) -> `http.ts:160` `hadSession` is true -> `onUnauthorized` ->
  `index.ts:72` `sessions.forget().then(onSessionLost)` -> `app.tsx:73` dispatches `ended` ->
  `Gate` renders `LoginScreen`. The chat's `useStepUp.confirm` loop (views/chat/writes/use_step_up.ts:61-71,
  "keeps asking after a wrong password") is unmounted underneath, and a second `stepUp` call
  rejects with "There is no session to confirm." The server session is never logged out.
- Evidence: `http.ts:160` `if (response.status === 401 && hadSession) deps.onUnauthorized();`.
  Throwaway test (`.claude/bug-review-2026-10-16/web_shell/stepup.test.ts.txt`, run inside the package then
  removed): login, step-up answered 401 -> `onSessionLost` called once, `loadSession()` null,
  a retry with the right password rejects `/no session/`. The existing
  `tests/views/chat/composer.test.tsx:151` passes only because its harness wires
  `onSessionLost: () => {}` and never mounts `Gate`.
- Fix sketch: do not treat a 401 from `/v1/auth/step-up` or `/v1/auth/step-up/challenge` as
  session loss (exempt those targets in `send`, or let `session.stepUp` pass a flag); only an
  `authentication_failed` on a non-step-up signed route means the session is gone.

#### F2. A UnifiedPush endpoint rotation or distributor unregistration is dropped; push dies silently   [severity: MEDIUM] [confidence: HIGH]
- File: packages/observation-web/android/app/src/main/java/dev/hivemind/observation/push/PushReceiver.kt:33-55; push/UnifiedPushPlugin.kt:91-96; src/landing_board/client/push.ts:68
- What: `onNewEndpoint` only saves to `PushStore` and completes the one waiting plugin call. When
  no `register` call is waiting (a distributor rotates the endpoint later, or the broadcast
  arrives after the 20 s `TIMEOUT`), nothing reaches JavaScript: no plugin method reads
  `PushStore` and no event is emitted, so `POST /v1/push/subscriptions` is never re-done.
  `onUnregistered` likewise clears only `PushStore`.
- Path to failure: push set up once -> distributor (ntfy) rotates the endpoint (server move,
  re-registration after an update) -> `NEW_ENDPOINT` broadcast -> `PushRelay.deliver` finds no
  waiter -> Entrance keeps pushing to the old endpoint -> 404/410 -> `courier.py:155-156`
  deletes the subscription -> the phone's `push_subscription` record still holds the old id, so
  `push.isRegistered()` is true and `PushCard` (share/setup/push_card.tsx:70) never re-offers.
  No notice reaches the phone until it logs out and in again.
- Evidence: `PushRelay.deliver` (PushStore.kt:55-59) is the only consumer; `PushStore.save` has no
  reader; `UnifiedPushPlugin` exposes only `register`/`unregister`.
- Fix sketch: have the receiver `notifyListeners("endpoint", ..., retain=true)` (and an
  "unregistered" event); on the TypeScript side re-register the new endpoint (delete the old
  subscription id first) and clear the stored id on unregistration.

#### F3. A 4401 on a stream reconnect leaves a dead session on screen as "no access"   [severity: MEDIUM] [confidence: MEDIUM]
- File: packages/observation-web/src/landing_board/client/socket.ts:74-76, 204-207; src/store/view_stream.ts:33-43
- What: the close-code table makes 4401 a permanent `refused` and only 4410/4411 end the session.
  The Entrance answers the hello of an expired, idled-out or revoked session with 4401 (not 4410:
  `streams/socket.py:191-193`, `authenticate_websocket` raises `AuthenticationFailedError`), and
  4410 is only sent by `_watch_session` to a socket that is still connected.
- Path to failure: phone or laptop goes offline (sleep overnight) -> the session idles out or
  expires on the Entrance -> network returns -> every stream reconnects with backoff -> hello ->
  4401 -> all three shell streams and the view's streams become `refused` -> strip shows
  "no access", top bar "offline"; nothing retries and no HTTP request is made on its own, so
  `onUnauthorized` never fires. The shell stays up on a dead session until the person clicks
  something that calls the Landing Board. `restore()` catches only the absolute `expires_at`, not
  the idle timeout.
- Evidence: `policyFor`: `case 4401: case 4403: return 'refused'`; `endSessionOnClose` returns
  unless reason is `session_ended`/`reduced`.
- Fix sketch: on a 4401 (at least for a stream that was previously `live`), probe
  `GET /v1/devices/me`; its 401 ends the session through the existing `onUnauthorized` path, while
  a 200 keeps the stream as a genuine refusal.

#### F4. The waiting notice misses notices across a reconnect and is never re-seeded   [severity: MEDIUM] [confidence: HIGH]
- File: packages/observation-web/src/share/waiting/use_waiting.ts:45-62
- What: `/v1/push/stream` is live-only (`push/websocket.py:111-134` returns GONE with no replay
  when no socket is attached), yet the hook reads `/v1/inbox` once per effect and ignores
  `onState`. Notices sent while the socket was down (backoff after a network drop) are lost.
- Path to failure: question waiting, ongoing notification shown -> phone loses network for a
  minute -> the question is answered from the laptop -> `withdrawn` notice is GONE for this
  device -> socket reconnects -> `state.refs` still holds the ref -> "A question is waiting"
  stays on the shade until another notice changes the count or the session restarts. The
  mirror case (a `question_waiting` missed) shows nothing. ADR-0050 says the notice is
  "withdrawn when the question is answered anywhere". Also, the seed unions with `state.refs`
  (line 59), so a `withdrawn` that arrives before the inbox read resolves is re-added.
- Evidence: `onState: () => undefined`; the inbox read runs only in the effect body.
- Fix sketch: on every `onState('live')` re-read `/v1/inbox` and replace `refs` with the
  inbox's question ids (plus notices seen since the read started); publish after.

#### F5. View and summary state survives logout; the next login shows stale counts   [severity: LOW] [confidence: HIGH]
- File: packages/observation-web/src/store/feed.ts:71-74; src/store/slices/session.ts:83-89; src/store/slices/summary.ts:216-233
- What: `startFeeds` resets only the connection slice, and no slice reacts to `left`/`ended`.
  `summary.streamed` stays true, so after a logout and a later login in the same page (a PWA
  that is never reloaded) the strip reads the stale `bees`/`cells`/`autopilot` maps instead of
  the fresh `/v1/summary` seed; a Warden that ended while logged out never sends the Heartbeat
  that would sweep its workers, so "Bees active" is too high indefinitely. `seed()` is also not
  cancelled, so a late answer lands in the store after the shell unmounted.
- Fix sketch: dispatch a `reset` to the summary (and view) slices from `startFeeds`, or reset
  every slice to `initial` on `left`/`ended` in the store.

### Looked at and found sound
- Signing: `requestString`/`socketString` match `x-hive-signing` (tests rebuild the worked
  example); the target signed is the encoded path plus `URLSearchParams` query exactly as sent, and
  the server verifies over `scope["raw_path"]` + `query_string` (`gate/admit.py:232-238`,
  `streams/socket.py:167-171`), so percent-encoding survives both ways.
- Key and token storage: P-256 key generated `extractable: false`, stored with the token in
  IndexedDB (memory fallback), never in localStorage; nothing logs the password, assertion or
  token; Android excludes the WebView data from cloud backup and device transfer
  (`allowBackup=false`, `data_extraction_rules.xml`).
- 401 handling on logout: a 401 from `/v1/auth/logout` races `left` and the late `ended` is
  ignored by the reducer (`status !== 'active'`).
- Socket reconnect policy matches `x-hive-socket-close`: the server sends 1000 only when the
  client itself left (`_client_left`, send failures), 1001 on hub shutdown (backs off), 4409 resumes
  at once with the chat cursor (`?after=<seq>`, which `streams/views/landing.py:90` accepts);
  `close()` clears timers and a stale socket's `onClose` is ignored (`this.socket === ws`).
  Cells reconnect re-sends every Cell and `foldCell` dedups by id.
- Write guard: every non-GET goes through `assertMethodAllowed` before signing; the pattern list
  covers only auth, chat, the image intake, inbox answers, goals, push subscriptions, the
  Entrance's own routes and passkey enrolment (`tests/shell/write_routes.test.ts` enumerates the
  document's mutating routes).
- Step-up required is 403 (`gate/errors.py:42`), so the chat's guarded retry is reached; after a
  successful step-up the token is not rotated (`SteppedUpView` carries only `stepped_up_until`),
  so keeping the stored token is right.
- `approved()`: the challenge route answers 401 alike for pending/denied/unknown, and the
  client only maps 401 to "not yet".
- Generated types: `pnpm generate:check` passes; enum values used by the summary slice
  (`ACTIVE`/`WATCH`, `REAL`, `READY`/`GRANTED`, `SPAWNED`/`RUNNING`/`HANDING_OFF`) exist in the
  document; telemetry rows of one Heartbeat share the Warden sample's `at`
  (`observation/views/frames.py:142-168`), which the `beats` bookkeeping relies on.
- PWA worker: every request under `/v1` bypasses the worker (`isApiPath`), so no authenticated
  response is cached; navigations are network-first with the cached shell as offline fallback;
  opaque/non-OK answers are not cached; the worker is not registered inside the APK.
- Android shell: `server.hostname` pinned with `androidScheme: https`, `cleartext: false`,
  `allowMixedContent: false`, `usesCleartextTraffic="false"`, no `allowNavigation`; the Entrance
  adds exactly `webview_origin` to CORS and socket origin checks when `[entrance.android]` is
  configured; passkeys use the Entrance's `rpId` through the Credential Manager; push payloads
  are never read (`onMessage` posts a constant notice); invite links are accepted only over
  https with no userinfo and path `/enrol`.
- Share intake: the intent is spent once (`activity.intent = Intent(ACTION_MAIN)`), bytes are
  bounded at 10 MB, the retained `shared` event reaches the first listener, and nothing is sent
  before the card's Send.
- Router: hash parsing rejects bad percent-encoding and loopback-only routes off loopback.

### Not covered
- `src/styles/*.css`, `src/components/*` (presentational; skimmed only), the e2e scripts under
  `e2e/`, and the Gradle wrapper/launcher resources.
- The Kotlin plugins were read against the connector/Credential Manager API from memory; the
  roadmap notes the native side has not been compiled, and I could not compile it here either.

---

<!-- full report: wardens -->

## Full report: Wardens review — 6 findings

### Summary
The Warden subsystem grew by ~13k lines: model-server hosting on the Warden's own Cell (8.3),
routing before the Guard (8.1), the Royal Jelly relay, Basket transfers and the Comb run (phase 9),
and the whole offline story (reconnecting link, durable outbox, freeze/resume, diagnostic Drone,
watch mode and Patrols, Nuc promotion) from phase 11. The risk concentrates in the offline package
(`offline/link.py`, `reconnect.py`, `resume.py`) and the background jobs that run beside the tick
(`ticks/basket/send.py`, `ticks/nuc/run.py`, `ticks/tools/ports.py`). The code is careful: every
state change goes through the transition table, every task is reaped, every send is guarded, the
CLAUDE.md invariants hold (nothing under `autopilot/` imports `hivemind.llm`, directly or
transitively; Wardens only lease/release through `RealCellSource` and stop every server they started
before a lease goes back). All 1074 unit tests under `tests/unit/wardens` pass. What I found are
mostly timing and lost-data edges on the offline path, not core logic errors; shippable with a short
pass on F1 and F2.

### Findings

#### F1. A Basket shipment up is lost for good when the Queen link blips   [severity: MEDIUM] [confidence: MEDIUM]
- File: packages/hivemind/src/hivemind/wardens/offline/policy.py:92-105; packages/hivemind/src/hivemind/wardens/ticks/basket/send.py:132-135, 170-182
- What: `basket.offer` and `basket.chunk` are not in the offline table, so while a reconnecting
  link is down they are REFUSED (`OfflineRefusedError` is a `ConnectionLostError`, which
  `send_guarded` logs and swallows). `on_answer` ignores `send_guarded`'s False for each chunk, and
  nothing re-offers a blob after the reconnection; the only exit is the 600 s request timeout.
- Path to failure: a Worker on a colonized device calls `retain`; `WardenRetainSink._settle`
  deposits locally and starts `_ship` (`retain.py:153`). `ship_up` sends the offer, the Queen acks,
  the Warden sends chunk N while the link is in its unnoticed-dead stretch or OFFLINE phase; the
  chunk is dropped (REFUSED) and `on_answer` returns as if sent. No ack ever comes; after
  `request_timeout_s` `_await_done` records `basket.refused code=timeout` and forgets the transfer.
  The blob stays resident in the Cell's store, the Queen never re-deposits it, so the Honey record
  that is "the only way a bee finds a blob" (roadmap 9.2a) is never written. The same holds for the
  package shipped before a CELL-scope sandbox request (`ports.py:172`), which then fails the flow.
- Evidence: `decide_offline` returns `REFUSE` for `BasketChunk`/`BasketOffer` (unit test
  `test_policy.py` covers only the listed kinds); `send.py:135` `await send_guarded(...)` with the
  result discarded; `_recover`'s `resend` only covers frames `must_arrive` kept, which excludes
  these; the receiving side's "resumes from the bytes already held" (`test_receive.py:220`) needs the
  sender to be asked again, and nothing asks.
- Fix sketch: after `rejoin` goes online, re-drive every `warden._basket.outgoing` by re-sending its
  offer (the receiver answers with the byte it holds), or queue `basket.offer`/`basket.chunk` as
  must-arrive frames; at minimum treat a False from the chunk send as a failed shipment at once
  instead of waiting out 600 s.

#### F2. The resume pauses (`RESUME_SETTLE_S`, `RESUME_RETRY_S`) never wake the tick   [severity: LOW] [confidence: HIGH]
- File: packages/hivemind/src/hivemind/wardens/offline/resume.py:97-105, 123, 158, 224-226; packages/hivemind/src/hivemind/wardens/offline/outage.py:233-242
- What: `_pause_resume` sets `outage.retry_after`, a deadline only the clock crosses, but
  `reconcile` feeds only the search's wait and the offline limit into `Outage.wake_in`; and for a
  Level 0 device Warden (`remote is None`) `wake_in` is never called at all, so `due_at` stays None.
- Path to failure: link returns (or `cell_found()` fires) → `leave_offline`/`_follow_cell` sets
  `retry_after = now + 2 s` → `_resume_held` returns early → nothing arrives → the next tick is the
  heartbeat (`heartbeat_interval_s`: 5 s for a Hive Stand-side device Warden, 15 s for an in-cell
  one). A refused re-lease (`_try_later`, 30 s) is likewise re-tried only on the beat after the 30 s.
  Tasks resume late by up to one heartbeat interval; the module's own invariant says the tick "is
  told to wake then, rather than at the heartbeat that happens to follow".
- Evidence: `test_resume.py::_past_the_settle` and `test_resume_cell_failure.py:124` both have to
  call `scene.outage.nudge()` by hand after advancing the clock, which is exactly the wake the code
  does not produce.
- Fix sketch: in `reconcile`, after `_follow_cell`/`_resume_held`, call
  `outage.wake_in(now, search_wait, _until_limit(...), _until_retry(outage))` for every outage
  (remote or not), where `_until_retry` is `(retry_after - now).total_seconds()` or None.

#### F3. A bee that dies with its Cell after checkpointing resumes from the older Handoff   [severity: LOW] [confidence: MEDIUM]
- File: packages/hivemind/src/hivemind/wardens/ticks/lease.py:270-274
- What: `wait_out_the_cell` calls `outage.hold(...)` only when the task is not already held; when
  it is (the usual case: `cut_cell` held it with `sub_bee.last_handoff` as of the pause), the row is
  retired directly without `outage.note_handoff`, so a HandoffRef the bee reported after the pause
  (`record_progress` updates `sub_bee.last_handoff`, heartbeat.py:337) is not carried into the hold.
- Path to failure: Level 0 device: `cut_cell` holds task T with handoff H0 (or None) and orders a
  HANDOFF; the bee, inside a command, first checkpoints (TaskProgress CHECKPOINTED → H1) and then
  its command fails with the link → WORKER_FAILED → `wait_out_the_cell` → `is_holding` True → skip
  hold → `retire_sub_bee`. On return `_resume_one` uses `held.handoff` = H0, not H1: the work since
  H0 is redone. `record_heartbeat`'s ended path (heartbeat.py:319-322) does call `note_handoff`,
  which shows the intent.
- Fix sketch: call `outage.note_handoff(sub_bee.task_id, sub_bee.last_handoff)` (or `hold` again,
  which replaces the entry) unconditionally before retiring.

#### F4. A Nuc promotion job and a TaskAssign can lease the device twice, raising a false CELL_UNREACHABLE   [severity: LOW] [confidence: MEDIUM]
- File: packages/hivemind/src/hivemind/wardens/ticks/nuc/run.py:96-98; packages/hivemind/src/hivemind/wardens/ticks/assign.py:350-365
- What: `live_host` runs in a background job and calls `open_lease` when the idle release left no
  lease; nothing stops the tick from calling `warden.start()` (via `_retry_lease_or_escalate`) while
  that `source.lease` round trip is in flight.
- Path to failure: idle colonized Warden (lease released) receives `swarm.nuc_promote` → job awaits
  `source.lease`; a `TaskAssign` arrives on the next tick → `cell_context` is None → `warden.start()`
  → second `source.lease` → `SwarmSource` raises `LeaseRefusedError("the device is already
  leased")` (swarm/source.py:310) → `_watch` records `warden.watch`, `_lease_retry` is set and, since
  `due is None`, `CELL_UNREACHABLE` is sent to the Queen (assign.py:358-365) although the device is
  fine; the job's lease lands a moment later and `spawn_parked` starts the task on it. The Queen's
  autopilot now handles an Alarm about a Cell that is reachable.
- Fix sketch: guard both lease paths with one `asyncio.Lock` on the Warden (or have
  `_retry_lease_or_escalate` park without alarming while `warden._tool_jobs` holds a promotion).

#### F5. `ChangeTracker` drops file changes past `cap` for good   [severity: LOW] [confidence: HIGH]
- File: packages/hivemind/src/hivemind/wardens/watch/probe/files.py:199-207
- What: `changes` returns only the oldest `cap` (200 by default) changes but `_remember`s the full
  new listing, so the dropped changes are no longer a difference on the next look and never appear.
- Path to failure: 600 files change under a watched root between two samples (a build, a log
  rotation); the sample carries 200 with `is_truncated=True`; the cursor advances to the 200th; the
  next sample compares against the listing that already contains all 600 → 400 changes are never
  observed by any Patrol. The docstring ("the oldest are kept so the cursor loses nothing") promises
  the opposite.
- Fix sketch: remember only the files whose change was reported (or the previous listing updated
  with the reported subset) so the remainder shows up as changes on the next look.

#### F6. A refusal arriving after 2 s installs a tool the Queen refused   [severity: LOW] [confidence: MEDIUM]
- File: packages/hivemind/src/hivemind/wardens/ticks/tools/remote_registry.py:63, 153-166
- What: a CELL-scope promotion announces `tool.promoted` and waits `REFUSAL_WAIT_S = 2.0` for a
  `control.error`; silence means accepted, and the announcer then installs the tool into every
  sub-bee's bundle (`WardenAnnouncer.announce` → `install_tool`). A refusal that arrives on the
  third second resolves no future (`relay.py:111` logs `unmatched_reply`) and changes nothing.
- Path to failure: a device over a slow or congested link to the Hive Stand (round trip plus the
  Queen's tick cadence above 2 s); the Queen refuses (a version already current in her registry, a
  REAL_CELL path she will not accept) but the Warden has already `_store`d the row and handed the
  tool to its bees, and the two registries now disagree on what is current for that Cell.
- Fix sketch: make the announcement a request the Queen acknowledges (`tool.promoted` is an event
  today), or at least scale the wait to the link's observed round trip and treat a late refusal by
  retiring the local row and re-attenuating.

### Looked at and found sound
- Invariants: no `hivemind.llm` import under `wardens/autopilot/` (the `workers` import is lazy and
  guarded, `lint-imports` enforces it); no `subprocess` in `wardens/`; the Comb sandbox choice and
  `is_borrowed` never branch on `cell.kind`; `ModelServerHost.stop_all` runs before every release
  (`Warden.stop`, `release_lease`, `handle_release_lease`), `_until_healthy` stops a half-started
  server on cancellation, and `release_idle_lease` keeps a lease while a job or a server runs.
- `offline/link.py` + `reconnect.py`: the online flip happens under the outbox lock with the queue
  drained; a Warden send during RECOVERING is queued, not lost; `redial` honours `abandoned` and
  closes a connection made after `close`; the local-close case ends the inner receive cleanly only
  when the peer completed the handshake, so `_recent.clear()` there is safe; the receive loop never
  ends before `close`, so the Warden's `_drain_items` cannot spin.
- `offline/freeze.py` + `resume.py`: every state change goes through `can_transition`/
  `assert_transition`; `cluster_fully` hands held tasks to `_clustered_tasks` and `leave_offline`
  `take()`s them; the Queen's cancel/pause/new attempt drops a hold (`_drop_hold`, `_supersede`);
  `_lease_again` refuses to re-lease a stopped or non-idle Warden.
- `offline/diagnose/*`: the search is bounded by `wall_clock_s` carved out of `max_offline_s`;
  a Drone takes no grant slot but is bounded by `Ceilings.max_sub_bees`; local rows are excluded
  from Heartbeats, acceptance, alarm handling and the Honey relay.
- `ticks/tools/relay.py`: first-hop identity (worker, task, warden, cell) is checked before any
  relay; upward kinds only from the sub-bee's link, downward only from the Queen's; an unreachable
  Queen answers the asker at once. `install.py` installs only a package that reads back to its hash
  and never widens a bundle (`visible_entries`).
- `ticks/basket/receive.py`: nothing is stored before hash and signature verify; an offer for
  another Cell is refused; a repeated shipment is acked complete; integrity failures reach the trail.
- `watch/`: only `watch_permits` kinds are asked for; roots are absolute, NUL-free, bounded;
  the two scripts take every variable as a positional parameter; sections are keyed by a random
  mark; the Patrol never deletes observations it did not review, SKIPPED keeps the window.
- `local_pool/hosting/sources.py` VRAM accounting is cumulative; `plan_loads` with no ceilings
  loads the manifest's list; `door.provider_door` never drops a manifest-declared provider.
- `spawn/route.py`: routing only reorders the slot's own chain and the Guard checks the routed key.

### Not covered
- The e2e suites (`tests/e2e/test_phase11_*`, `tests/e2e/offline/`) were read for intent but not
  run (they need containers/sockets); `hivemind.swarm.watch.PollenWatchProbe`, `queen/attach` and
  `cli/compose/swarm` were only read where a finding depended on them.
- Pre-existing modules changed only cosmetically (`ticks/questions.py`, `isolation/taint.py`,
  `spawn/attenuate.py`'s `widen_lease_reachability`, `trail_sync.py`'s signing) were skimmed, not
  re-reviewed line by line.
- `offline/diagnose/assign.py`'s objective text and `llm/prompts/patrol.md` were not assessed for
  prompt quality, only for what they leak (nothing beyond the peer host name).

---

<!-- full report: pollen -->

## Full report: Pollen Packet and Cell layer review — 4 findings

### Summary

The Pollen Packet (`packages/pollen`) is the device-side gateway: enrolment, the connection loop,
the lease table and state file, the four executors, the dead-man switch and the Warden keeper. The
`hivemind.cell` additions are a lease-free read-only `ObserverSession`, the recycled-pid
`forget_started_process` plumbing, and the shared `cell.tunnel` VPN judges; `hivemind.hive` adds
Propolis/Night-Veil attestation and a random hostname for Night Veil containers. The architecture
rule holds: `pollen` imports nothing from `hivemind` (only `waggle`), confirmed by grep. The risky
machinery is largely careful — subprocess groups are recorded before output, killed on every exit
path and signalled by pid+creation-time so a recycled pid is never hit; the state file and lease
table are disk-first with atomic replace and Windows sharing-refusal retries; the `ObserverSession`
genuinely refuses every write; the quota walk never follows a symlink and reads each file's live
size. The defects I found are concentrated in two places: the put-file transfer trusts its declared
size and never re-checks the quota mid-stream, and Windows process-tree handling uses `taskkill /T`
rather than the Job Object the contract names, so a detached child can escape a kill. Both are real
but need a misbehaving sender or a detaching program; the subsystem is close to shippable with those
addressed.

### Findings

#### F1. `session.put_file` enforces the scratch quota only on the first chunk   [severity: MEDIUM] [confidence: HIGH]
- File: packages/pollen/src/pollen/executors/transfers.py:505-543, 592-627, 629-645
- What: The quota and free-disk check (`check_room`) runs once, in `_start`, against the sender's
  *declared* `total_bytes`. Every following chunk is appended with no re-check, and continuation
  only requires `offset == transfer.written` — nothing caps `written` at `total`. A transfer can
  therefore write far more than it declared (or than the quota allows) before any terminal check.
- Path to failure: A Warden (or anything speaking the gateway contract — this check is the gateway's
  "second wall", ADR-0051) sends a first chunk with `total_bytes` small enough to pass `check_room`,
  then keeps sending chunks at the running offset without a `final` flag. Each `accept` →
  `_check_continuation` (offset matches) → `_append` writes the bytes; `_verify` (`written != total`)
  only runs on the final chunk, which never arrives. The partial file lives in scratch and grows
  without bound. `directory_size_bytes` is only consulted by the exec watchdog and by `check_room`
  at start, never during a transfer, so the lease's scratch quota and the device's
  `DISK_HEADROOM_BYTES` are both blown.
- Evidence: `_start` calls `check_room(scratch, lease.quota_bytes, payload.total_bytes, ...)` once;
  `accept`'s loop does `await asyncio.to_thread(_append, transfer.temp, payload.chunk)` with no size
  or room check; `_check_continuation` tests only sender/lease/path and `payload.offset !=
  transfer.written`. The contract (gateway-contract §9.1) states "A `session.put_file` that would
  take the directory over the quota is refused with `pollen.quota_exceeded`", and quota.py's own
  docstring claims a put that would cross the quota "is refused before a byte is written" — true only
  for the honestly-declared total.
- Fix sketch: In `_check_continuation` (or before `_append`) reject when `transfer.written +
  len(chunk) > transfer.total`, and/or re-run `check_room` against the bytes actually landing so the
  quota holds whatever the declared total was.

#### F2. Windows process trees are killed with `taskkill /T`, not a Job Object; a detached child escapes   [severity: MEDIUM] [confidence: MEDIUM]
- File: packages/pollen/src/pollen/platform/windows.py:131-165 (`group_alive`, `signal_group`), and
  the contract at docs/waggle/gateway-contract.md:111-112, 486-489
- What: The contract promises every exec runs "in its own process group (POSIX) or Job Object
  (Windows)" and that "a program's children die with it". The Windows shim instead spawns with
  `CREATE_NEW_PROCESS_GROUP` (only a CTRL-event grouping) and ends a tree with `taskkill /T`, and
  `group_alive` inspects only the recorded leader (and, while the leader is a zombie, its live
  `children(recursive=True)`). A descendant that detaches or whose intermediate parent has already
  exited is not in the tree `taskkill /T` walks and is invisible to `group_alive`.
- Path to failure: A lease's command on a Windows device spawns a grandchild and the direct child
  exits (or the grandchild is started detached). On release, `stop_group` → `signal_group(KILL)` runs
  `taskkill /T /F /PID <leader>`, which kills only the surviving tree reachable from the leader; the
  detached grandchild survives. `group_alive` then returns `False` (leader gone, no zombie), so the
  releaser counts the lease restored and the packet reports the device "left as found" while a
  process it started is still running — exactly what the POSIX `_members` path guards against by
  scanning the whole process group by pgid.
- Evidence: `group_alive` → `psutil.Process(ref.pid)`; only on `STATUS_ZOMBIE` does it look at
  `leader.children(recursive=True)`. `signal_group` → `self._runner(["taskkill", "/T", *flags,
  "/PID", str(ref.pid)])`. No Job Object is created anywhere (grep for `JobObject`/`job object` in
  `cell/` and `pollen/` finds only the docstring reference in windows.py). The POSIX shim, by
  contrast, enumerates group members (`_members`, `os.getpgid`) so a reparented descendant is still
  caught.
- Fix sketch: Assign each spawned child to a Windows Job Object and probe/terminate the job (the
  mechanism the contract names), so a detached descendant is both seen by `group_alive` and killed on
  release.

#### F3. Output cap can be overshot when stdout and stderr fill concurrently   [severity: LOW] [confidence: HIGH]
- File: packages/pollen/src/pollen/executors/run/streams.py:98-119 (`OutputSink.send`)
- What: `send` computes `room = self._plan.max_output_bytes - self._sent`, slices the data, `await`s
  the reporter in `_frame`, and only then does `self._sent += len(data)`. The two pump tasks (one per
  stream) run on the same loop and interleave across that `await`, so both can read the same stale
  `self._sent`, each send up to the full remaining room, and together exceed `max_output_bytes`.
- Path to failure: A command writes heavily on both stdout and stderr. The stdout pump enters `send`,
  computes room, and awaits `_frame` (→ `reporter.send` → `link.send` under a lock). While it is
  suspended, the stderr pump enters `send`, reads the not-yet-incremented `self._sent`, computes the
  same room and sends another near-full chunk. The cap is overshot by up to one read chunk
  (`READ_CHUNK_BYTES`, 64 KiB) per stream before `on_output_cap` fires.
- Evidence: `room = self._plan.max_output_bytes - self._sent` ... `await self._frame(...)` ...
  `self._sent += len(data)`; `_supervise` creates two concurrent `sink.pump` tasks sharing one
  `OutputSink`. No lock serialises `send`. (Offsets are per-stream so they are not corrupted; only
  the shared `_sent` cap is affected.)
- Fix sketch: Increment `self._sent` before awaiting `_frame` (reserve the room first), or guard
  `send` with an `asyncio.Lock`.

#### F4. A lease-table write failure in `_open` leaves an orphan scratch directory   [severity: LOW] [confidence: MEDIUM]
- File: packages/pollen/src/pollen/agent/leases.py:582-599 (`_open`), 657-663 (`_make_scratch`)
- What: `_open` creates the lease's scratch directory (`_make_scratch`) before persisting the lease
  (`deps.leases.put`). If the atomic table write raises `StateError`, the lease is never recorded,
  but the scratch directory is already on disk; nothing ever removes it, because the startup sweep
  only releases leases found in the table.
- Path to failure: A `cell.request` arrives, `_make_scratch` succeeds, then `leases.put` hits an
  `OSError` on the device's disk (full disk, a Windows sharing refusal that outlasts the retries) →
  `StateError`. Dispatch answers `control.error`. The `<data>/leases/<lease_id>` directory remains
  with no table entry, so neither this run's release nor a later packet's `release_all`
  (ORPHAN_SWEEP, which iterates `leases.all()`) ever reaches it — a residue on a device the Hive
  promised to leave exactly as found.
- Evidence: `scratch = await asyncio.to_thread(_make_scratch, ...)` then `lease = DeviceLease(...)`
  then `await deps.leases.put(lease)`; `_sweep` → `release_all` iterates `self._deps.leases.all()`,
  which is the persisted table only.
- Fix sketch: Record the lease first (or create scratch only after `put` succeeds), or remove the
  freshly-made scratch directory if `put` raises before answering.

### Looked at and found sound

- `pollen` imports nothing from `hivemind` (grep confirms; only `__init__`/docstring mentions).
- Subprocess kill-on-every-path: `ExecRun._run`/`_supervise` kill the group on cancellation, on a
  failed lease-table record, on a cap, a timeout, a stdin signal and a release; `stop_group` always
  forces after the grace even following a polite exit; POSIX `signal_group`/`group_alive` guard the
  recycled-pid and born-before-leader cases by creation time and pgid.
- State file / lease table durability: `write_atomic` is write-new → fsync → `when_free(os.replace)`
  with a directory fsync, and both `StateStore` and `LeaseTable` are disk-first/memory-second under a
  lock, so a failed or Windows-refused write never loses or duplicates a stored lease; `load`
  distinguishes missing (empty) from unreadable (`StateError`).
- Dead-man switch: monotonic-only elapsed measurement, restored gap clamped at zero for a backward
  clock, trips only at `GATEWAY` level with open leases, and `heard()` only on a link open a full
  ping interval or a fresh admitted Hive-node frame.
- `ObserverSession` refuses `put_file`/`get_file`/`delete_file`/`start` unconditionally and holds no
  lease; the recycled-pid `forget_started_process`/`_forget_if_gone` plumbing only forgets a pid once
  its whole group is gone.
- Scratch-quota arithmetic in `lease/quota.py` and `local/quota.py`: symlink-free walk, per-file
  `os.lstat` (not the cached directory listing), vanished dirs count zero, `check_room` adds
  `DISK_HEADROOM_BYTES`. (The exec watchdog path is correct; the gap is the put-file path, F1.)
- Signing/admission: enrolment writes the key before the first dial, sends the token only on a
  pin-matched connection, trusts only the invite-pinned node until the accept, and rejects an accept
  that re-keys the pinned node or over-grants access; the dispatcher verifies sender/freshness/replay
  and the Queen-only-kind rule; `accept_moved` requires a strictly-higher sequence from a Hive node
  and never swaps a key under a known node id or changes the pin's scheme.
- Night-Veil DNS/WebRTC/hidden-service/hostname judges and the shared `cell.tunnel` route parsers and
  judges are pure, fail-closed on unreadable input, and reuse the tunnel DNS judge rather than
  copying it; the Tor-disabled and Propolis completeness checks treat missing/NOT_APPLICABLE as FAIL.

### Not covered

- `packages/pollen/tests/`, `tests/unit/cell`, `tests/unit/hive`, `tests/e2e` were not run (reviewed
  for behaviour by reading source only); I did not execute pytest.
- The macOS `netstat`/`scutil` route parsers in `cell/tunnel/routes.py` were read but not
  exhaustively checked against real `netstat -rn` output for every flag/abbreviation case.
- `pollen/bootstrap/runtime.py` PREPARE and the `hive/backends` Docker/QEMU hostname wiring were read
  at a high level; I did not deeply audit the QEMU cloud-init hostname path.

---

<!-- full report: swarm -->

## Full report: Swarm / Entrance / Waggle review — 4 findings

### Summary
The swarm package is the Hive Stand's side of every device: a TLS-pinned WebSocket listener whose codec verifies every frame against a `KeyRing` of enrolled node keys (plus the one self-signed `swarm.enrol_request`), an enrolment door that redeems single-use invites atomically in SQLite, a liveness monitor over a compare-and-set status machine, `PollenSession` (a `CellSession` over `session.*`), and pure decisions for the runtime ladder. The entrance diff adds read routes (Honey browser, screens, Prometheus), one write (image intake) and the Android client's origins; waggle adds the frame ceiling, replay window, the sender's resend ring, TLS pinning and the 1.13 message families. I traced the security claims against the code: approval/enrolment/operator routes are declared `LOOPBACK_ONLY` and a unit test (`tests/unit/entrance/test_app.py::test_every_loopback_only_row_is_not_served_on_the_remote_listener`) enforces it; the remote listener is still built only when the exposure plan names one; enrolment denials carry ids and a reason code and the invite token is only ever hashed; device paths are judged by text in the device's own spelling (probed on both flavours, see below). Overall impression: shippable. I found no CRITICAL or HIGH defect; the four findings below are low-likelihood resource/lifecycle edges. Test runs: waggle 2690 passed (spec drift included); swarm unit 559+571 passed; entrance unit 1582 passed.

### Findings

#### F1. A device lease opened on the device is lost if the Hive's `cell.leased` trail write fails   [severity: LOW] [confidence: HIGH]
- File: packages/hivemind/src/hivemind/swarm/source.py:238-249
- What: `SwarmSource.lease()` asks the packet to open the lease (`_open_on_device`), builds the `RealCellLease`, then `await lease.open()`, and only after that sets `self._tenancy`. `RealCellLease.open()` (cell/lease.py:368-372) moves to OPEN and then `await self._record("cell.leased", ...)`; a trail write failure raises out of `lease()` after the device already holds an OPEN lease that nothing on the Hive Stand references.
- Path to failure: device answers `cell.lease_opened` → `lease.open()` raises (SQLite busy/disk full) → exception propagates (it is not `LeaseRefusedError`, so `wardens/ticks/lease.py:open_lease` does not catch it either) → `_tenancy` stays None → the next `lease()` passes `_check` and sends a second `cell.request`; the packet refuses a second lease on the same Cell → `LeaseRefusedError` → the Warden sits in WATCH. The device keeps its scratch directory and lease table until its dead-man fires, which only happens when the link drops. The device is not left as found while the link is healthy.
- Evidence: `lease = RealCellLease(...)` / `await lease.open()` / `self._tenancy = _Tenancy(lease, releaser)` (source.py:240-248). The Hive Stand's own source (`cell/local/source.py:195-207`) has the same ordering, so this is a shared pattern rather than a swarm-only slip; on a device the consequence is worse because the releaser is remote.
- Fix sketch: wrap `await lease.open()` in a try/except that sends a `cell.teardown_request` (or records the tenancy first so the next release path can give it back), then re-raise.

#### F2. `admit` racing a dying old link can leave a connected device UNREACHABLE in the registry until its next heartbeat   [severity: LOW] [confidence: MEDIUM]
- File: packages/hivemind/src/hivemind/swarm/gateway/heartbeat.py:159-198; routing.py:223-256
- What: `HeartbeatMonitor.admit` reads the node, touches it, and only moves UNREACHABLE→ONLINE when the status it read was UNREACHABLE; otherwise it records `swarm.online` without a move. `link_down` for the previous link reads the node and moves ONLINE→UNREACHABLE. Nothing orders the two: `admit` runs before `hub.attach`, so `detach` in the old task can still return True while the new connection is mid-admission.
- Path to failure: old link ends on its own (keepalive) at the same moment the device's new connection is admitted. Old task: `detach` (True) → reads ONLINE → `_move(UNREACHABLE)` commits. New task: `admit` read ONLINE before that commit → `_record_online` only, no move → `attach` + `announce_online`. Old task then `announce_offline`. End state: registry says UNREACHABLE while the hub holds a live link; `reconcile.py:166-169` reads both and treats the device as unreachable ("UNREACHABLE wins"), so the unreachable hold starts for a live device. `_on_heartbeat` repairs it at the next beat (≤ `heartbeat_interval_s`), announcing online a second time.
- Evidence: `current = await self._registry.node(...) or node` … `if current.status is NodeStatus.UNREACHABLE: moved = ...` else `_record_online` (heartbeat.py:171-183) versus `current = await self._registry.node(...)`; `if current.status is NodeStatus.ONLINE: current = await self._move(...)` (heartbeat.py:195-197). The CAS keeps the registry consistent; only the announce ordering is wrong. The window is two DB round trips, so it needs a coincidence (a packet restart does not hit it: the new dial is hundreds of ms behind the old close).
- Fix sketch: in `admit`, after `touch`, re-read the node and move to ONLINE whenever it is not ONLINE (the CAS already makes a lost race harmless), or do the attach before the admit so `detach` refuses the stale link.

#### F3. Unauthenticated enrolment attempts each cost a Pheromone Trail row with no per-source limit   [severity: LOW] [confidence: MEDIUM]
- File: packages/hivemind/src/hivemind/swarm/enrolment/door.py:188-207, 236-244; gateway/routing.py:202-211
- What: any peer that can reach the swarm listener and self-sign a `swarm.enrol_request` (the verifier accepts that one frame from an unknown node by design) gets a registry read and a `swarm.enrol_refused` trail write for `UNKNOWN_INVITE`, then a closed connection. The only limit is the per-connection frame ceiling; a new connection per attempt is unlimited, and the listener has no per-address bucket like the Entrance's.
- Path to failure: a host on the overlay (codingrules 15 says the remote Waggle listener is bound to the overlay interface, so this is an insider or a compromised device, not the open internet) loops connect→enrol_request→close; each iteration appends to the append-only trail and the Guard Bee has no pattern for `swarm.enrol_refused`.
- Evidence: `_refuse` always `await self._recorder.record(event)` before raising (door.py:243); `serve` closes and the accept loop takes the next connection (listener.py:291-298) with no bucket.
- Fix sketch: count refusals per remote address in `TransportRefusals`/the listener and stop recording (or stop accepting) past a burst, or give the Guard Bee a `enrol_refusal_burst` pattern like `replay_refusal_burst`.

#### F4. `certificate_pin` reads the PEM as ASCII, so a certificate file with a non-ASCII comment refuses to start the listener   [severity: LOW] [confidence: HIGH]
- File: packages/hivemind/src/hivemind/swarm/gateway/listener.py:89
- What: `cert_path.read_text(encoding="ascii")` raises `UnicodeDecodeError` on any byte ≥ 0x80, while `ssl.load_cert_chain` (used by `server_ssl_context`) accepts such a file; a PEM exported from PKCS#12 commonly carries "Bag Attributes / friendlyName" lines with the owner's name.
- Path to failure: operator sets `[swarm] tls_cert` to such a file → `ListenerConfig.tls_pin` / `hive swarm invite` or the composition root raises `UnicodeDecodeError`, an exception no caller maps, so the Hive fails to start or to mint an invite with a traceback instead of a `SwarmError`.
- Evidence: `match = _PEM_CERTIFICATE.search(cert_path.read_text(encoding="ascii"))`.
- Fix sketch: read bytes and search with a bytes regex (or `errors="ignore"`), and wrap the decode in the existing `SwarmError`.

### Looked at and found sound

- Verifier and key ring: a frame from a known node always verifies against the pinned key, so a forged `swarm.enrol_request` claiming a known node id fails; an unknown node's only acceptable frame is a self-consistent enrol request whose payload `node_id`/`device_id` equal the envelope's and whose key signs it; `sender` is bound to `{device_id, warden_id}` on every frame; `pinned()` cuts a connection off at the first frame signed by another node. Revocation removes the key (`KeyRing.sync`) and `_is_this_device`/`_is_this_warden` refuse REVOKED besides.
- Enrolment: token hashed on arrival, never stored or put in an event or error; `_redeem` re-judges inside one `BEGIN IMMEDIATE` transaction with identity uniqueness (five unique columns) so two devices with one token or one key cannot both win; lapsed invites move to EXPIRED and the refusal path spends nothing; NIGHT_VEIL refused at mint, model and desk. `LAPSED_INVITES_SQL`'s text comparison of ISO timestamps is safe because `UtcDatetime` normalises to `+00:00` and the "+"/"." ordering agrees with time order.
- Liveness: CAS status moves with the event in one transaction; a stale link ending after a reconnect takes nothing offline (`hub.detach` identity check); a link that is open but silent is marked by the sweep and restored by the next heartbeat; the second `on_offline` for a link that ends after the sweep marked it is intended and tested.
- Links and exchanges: single reader per connection, frames routed by correlation id into bounded inboxes, `close()` drains so a blocked reader is never stuck, `fail_all` on link end, send lock with a timeout; `SwarmSource.on_frame`'s dead-man release cannot deadlock with a Warden's in-flight release (state check and lock acquisition have no suspension point between them, and RELEASING is skipped).
- Device paths: `DevicePathRule`/`device_text` probed with a throwaway script (.claude/bug-review-2026-10-16/swarm/paths_probe.py.txt): Windows roots compare case-insensitively, siblings (`scratch` vs `scratch2`) and other drives are outside, `..` collapses on both flavours, a Windows `Path` with no drive is given back as POSIX text, `absolute_device_path` refuses `C:logs` and relative names, `_outermost` drops nested and case-variant duplicates. The Guard's `fill_scratch` normalises backslashes so a Windows device's scopes match.
- `exit_code_for`: SIGKILL is -9 on any host; `_signal_code` never raises for a foreign name.
- Replay window, frame ceiling, resend ring: duplicate dropped only after decode+verify, per (node_id, id), bounded; ceiling counted before parsing; `due()` offers only frames younger than the receiver's window minus a margin; refusals are reported before the answer and recorded by the swarm listener with the reconnection grace read from the hub's attach time (same clock).
- TLS pinning: pin-only client context is used only together with `check_pin`, which aborts without a close frame on mismatch and is not in the retry list; server context TLS 1.2+.
- Waggle catalogue vs docs/waggle/spec.md: `test_spec_drift.py` passes (89 kinds, shapes, replies_to), version 1.13 on both sides; new message validators (`Observe`, `Colonize`/`Colonized`, `SessionProcess`/`SessionProcessState`, `TaskAssign.handoff`, `TrailSegmentSync.segment_signature`, `SessionExit` for DELETE_FILE) are consistent in both directions; older peers still validate because every new field defaults.
- Entrance: approval, invite, operator and device-approval rows are `LOOPBACK_ONLY` and tested not to exist on the remote listener; no exposure mode was added; the new rows are reads under `observe` (`observe:thoughts` + C2 for thoughts) and one write under `entrance:submit`; the Honey item route checks scope and clearance at the gate, tells a refused client only the family, and re-reads through the viewer's own reader; images are checked (type, size, signature bytes, byte budget) before any deposit, deposited as C2 only, and never logged; the Android WebView origin and `assetlinks.json` appear only when `[entrance.android]` is configured; `ByteBudget`/`RateLimiter` refactor keeps the bucket arithmetic unchanged and the manifest pins `image_bytes_per_minute >= max_image_bytes`.

### Not covered

- `packages/hivemind/src/hivemind/swarm/errors.py`, `operator/errors.py`, `registry/protocol.py` (read for attribute names only).
- The device side (`packages/pollen`) and the Queen's swarm gate (`queen/swarm_gate/`) were read only where a swarm finding depended on them.
- `entrance/reads/screens/*` and `observation` view builders beyond `gather.py`, `cost.py`, `cell_page.py`, `metrics.py`; the Observation Hive front end.
- `waggle/messages/basket/*` and `tool/quarantine.py` were read at validator level only.

---

<!-- full report: web_views -->

## Full report: Observation Hive views review — 4 findings

### Summary
`packages/observation-web/src/views/` holds twelve React views (thoughts, cell, forage, fleet,
attendant, chat, cost, capping, honey, login, enrol, devices), each a pure slice reducer plus a
hook that seeds from one read, folds one or more Entrance streams through the store's
`useViewStream`, and re-reads once per burst. The risk concentrates in the chat (the UI's one
write path, with push-to-talk and the step-up flow) and in the hooks' lifecycle handling. The
reducers are careful: every list is bounded, repeats are deduplicated (by id, by `(id, at)`, or
by a cursor), stale answers are dropped by principal/path/cell id, and a frame that beats the
seed is reconciled. `pnpm typecheck`, `pnpm lint` and `pnpm vitest run tests/views` (495 tests)
are green at HEAD. The one real defect is in push-to-talk: a hold that is still opening the
microphone when the chat unmounts leaks the recording, the countdown and, at the 60 s cap, a
network post. The rest are low-severity branch mistakes around step-up. Shippable with the
push-to-talk fix.

### Findings

#### F1. Push-to-talk keeps recording and posts a clip after the Chat view unmounts   [severity: MEDIUM] [confidence: HIGH]
- File: packages/observation-web/src/views/chat/voice/use_hold.ts:96-111 (`recording.current =
  await recorder.start()` at 96, `countdown.start()` at 105, the unmount cleanup
  `recording.current?.cancel()` at 111)
- What: the unmount cleanup only cancels a recording that has already started
  (`recording.current?.cancel()`). A hold whose `recorder.start()` is still pending (the
  browser's microphone permission prompt is up, or `getUserMedia` is slow on a phone) is not
  tracked, so when `start()` resolves after unmount the dead hook stores the recording, moves to
  `recording`, and starts the countdown interval. Nothing ever cancels it: the microphone stays
  open (browser indicator on) with no UI, and when the countdown reaches 60 s `ending.current()`
  runs `end()`, which stops the recorder and calls `onClip`, i.e. `actions.speak(clip, intent)`,
  posting 60 s of unseen audio to `POST /v1/chat/audio` with whatever intent was last chosen
  (the default is `goal`, echoed back for confirmation; `chat` goes straight to the Queen's inbox).
- Path to failure: first use of the talk button on a phone; pointerdown opens the permission
  prompt; the person taps a nav link (view unmounts, `recording.current` is null so the cleanup
  does nothing); the person then allows the microphone; `begin` continues on the unmounted hook
  (`live.current.released` is false, so it does not stop), `countdown.start()` sets an interval
  after `useCountdown`'s own cleanup already ran; 60 s later `end()` stops the recorder and sends
  the clip.
- Evidence: reproduced with a throwaway `renderHook` test (fake recorder whose `start()` resolves
  after `unmount()`, fake timers advanced 61 s): `cancel` is never called, `stop` is called once
  and `onClip` is called once after unmount. The existing voice tests cover release-before-open
  but not unmount-before-open. Code:
  `recording.current = await recorder.start(); ... if (live.current.released) return void end();
  go('recording'); countdown.start();` with the cleanup `recording.current?.cancel()`.
- Fix sketch: keep an `alive` ref set false in the unmount cleanup; after `await recorder.start()`
  in `begin`, if `!alive.current` call `cancel()` on the new recording and return (and do the same
  in the cleanup for a start that resolves later by storing the pending promise).

#### F2. A step-up done from the Chat never clears the session's `needsStepUp`, so the shell banner stays   [severity: LOW] [confidence: HIGH]
- File: packages/observation-web/src/views/chat/writes/use_step_up.ts:66-67 (`confirm`: `await
  client.session.stepUp(typed); return;`)
- What: the chat's `useStepUp` duplicates `src/views/devices/step_up/use_step_up.ts` but drops the
  `store.dispatch({ slice: 'session', event: { type: 'stepped_up' } })` the devices version makes
  after a good step-up (devices version line 108). `client.session.stepUp` updates the client's
  holder and IndexedDB only; the session slice keeps `needsStepUp: true`.
- Path to failure: a session opened under the travel lock (`needs_step_up` true at login); the
  person sends a chat message, gets the prompt, types the password and the step-up succeeds; the
  message goes through, but `src/shell/step_up_banner.tsx` ("This device is on a new network.
  Confirm it is you") stays for the rest of the session, and the Devices/Enrol screens'
  `StepUpPanel` (which keys on the same flag) keeps offering a second, redundant step-up form.
- Evidence: `src/store/slices/session.ts` only clears the flag on `stepped_up`; grep shows that
  event dispatched from `devices/step_up/*` and nowhere under `chat/`.
- Fix sketch: dispatch `stepped_up` after `client.session.stepUp` succeeds in the chat's `confirm`
  (or make the chat import the devices hook and delete the duplicate).

#### F3. The Chat treats a `step_up_required` 403 on its read as "may not submit" and hides the only step-up prompt it has   [severity: LOW] [confidence: MEDIUM]
- File: packages/observation-web/src/views/chat/chat_screen.tsx:56,76 (`const refused =
  state.error instanceof LandingBoardError && state.error.status === 403;` ... `{refused ? null :
  <Dock .../>}`)
- What: `refused` is meant for `capability_denied` (no `entrance:submit`), but the Entrance's
  travel lock answers every non-exempt request, reads included, with `403 step_up_required`
  (`entrance/gate/admit.py:193`, `auth/step_up/rules.py` "a session the travel lock flagged needs
  step-up for every request"). The chat read then fails with that code, the view hides the Dock,
  and the Dock is where `StepUpPrompt` and the composer live, so the screen shows "This needs you
  to confirm again. Enter your password..." with nothing to type it into, and the stream is
  refused (4403, not retried) with no re-seed after the person steps up elsewhere.
- Path to failure: log in from a phone on a new network, open `#/chat` first (or be on it when the
  session is restored): the read is refused, the composer and prompt vanish; the person must find
  the banner link to `#/devices`, step up there, then leave and re-enter the chat for it to seed.
- Evidence: the branch checks only `status === 403`; `needsStepUp(error)` in
  `writes/use_step_up.ts` already distinguishes the code and is not used here.
- Fix sketch: `refused` should require `error.code === 'hivemind.entrance.capability_denied'`
  (or `!needsStepUp(error)`); on a step-up refusal keep the Dock, run the seed through `guarded`,
  and re-seed after the step-up succeeds.

#### F4. The Thoughts view's "open this view again to read further back" cannot be followed   [severity: LOW] [confidence: HIGH]
- File: packages/observation-web/src/views/thoughts/slice.ts:112,121,189 (`bounded`, `seeded`,
  reducer `open`) and src/views/thoughts/thoughts_view.tsx:94 (the `state.truncated` note)
- What: once the 500-record bound cuts the list, `truncated` is set, `nextCursor` is nulled and
  the Older button disappears; the note tells the reader to open the view again. Reopening the
  same bee dispatches `open` with the same principal, which keeps the state (including
  `truncated: true`, `nextCursor: null`), and `seeded` keeps `state.nextCursor` when already
  seeded. The only ways back are to open a different bee first or reload the page.
- Path to failure: a busy Queen (more than 500 episodes plus decisions in the window); the reader
  pages Older ten times or follows for long enough, sees the note, navigates away and back to
  `#/thoughts/queen`: no Older button, same note.
- Evidence: `case 'open': if (event.principal === state.principal) return state.error ? {...} :
  state;` and `const nextCursor = state.seeded ? state.nextCursor : page.next_cursor;`.
- Fix sketch: on `open` for the same principal, reset `truncated` and let the seed's
  `next_cursor` through (or drop the bound's older half instead of nulling the cursor).

### Looked at and found sound
- Stream lifecycle: `useViewStream` opens on mount and closes on unmount with module-level
  `toEvent`/`resume` values in every view, so no stream reopens per render; the chat passes
  `CHAT_RESUME` so backoff and 4409 reconnects resume from `?after=<seq>`; `withQuery` drops null
  values so `cursor: null` is never sent.
- Duplicate and out-of-order handling: chat lines merged by id and sorted by `seq`; cost `seen`
  ids (500); capping `(id, at)` and the `moment` check; thoughts by id with `newestFirst` at seed;
  forage requests by event id and revoked ids remembered (`gone`); honey/cell/attendant/devices
  re-read on ticks with one timer per burst and the timer cleared on unmount/route change.
- Stale answers: thoughts, honey, cell and attendant reducers drop a seed/page/failure for a
  principal, path or Cell no longer open; the screens render "Reading…" between a route change
  and the slice's `open` (`mine` checks), so one Cell's/bee's data is never shown under another.
  Telemetry folds only samples whose `warden_id` matches the page's Warden; cell frames only for
  the page's id.
- Chat writes: the write set is chat, audio, the two inbox routes and goal confirm/decline (test
  enumerated); optimistic pending rows settle on the returned line id whether the line arrives
  before or after the POST answers; 409 on answer/acknowledge removes the item with a notice;
  cancelled step-up returns the original refusal; a wrong password remounts the prompt by round.
- Push-to-talk otherwise: single `MediaRecorder` per hold, release-before-open stops at once,
  release and the 60 s cap both stop the countdown, tracks are released by the recorder seam on
  stop/cancel/error, `touch-action: none` and context-menu suppression on the hold button.
- Cost arithmetic: no division by zero (`sharePercent`, `Gauge`, `withFractions`, `fractionOf`,
  flow `stack`/`slices`, capacity `layoutSegments` all guard); the client fold matches the server's
  formulas (`hosted = cost > 0`, saving = hosted); `cpu_load` is load/cores server-side so
  `busy = cpu_load * cores` is right; bytes/figures formatting fine.
- Pagination: thoughts limit 50 ≤ 200, honey tree re-read limit ≤ 200, search 10 ≤ 50, chat 100 ≤
  500; `older`/`more`/`found` only accept a page fetched with the cursor that is still next.
- Time: ISO expiries built in UTC, `TimeStamp` renders local time with the ISO in `title`,
  `useLapsed`/`minutesLeft` handle NaN.
- Rendering: no `dangerouslySetInnerHTML`/`innerHTML`; the QR SVG goes through an `<img>` data URL;
  held-request payloads and item content render as text; hrefs encode ids.
- Client-side gates: `EnrolScreen` and the Devices door controls are hidden off loopback and the
  server does not mount those routes remotely; `interactive` is only sent for Ed25519 keys.
- Attendant layout (`layersOf`) terminates on cycles; fleet sort is stable with nulls last.

### Not covered
- Stylesheets (`*.css`), `cell/diagram/glyphs.ts`, the `README.md` files under views, and the
  Android/Capacitor shell, share intake, shell, router, store internals and the Landing Board
  client beyond what the findings above depend on (sibling reviewer).
- Dev-only React StrictMode double-effect behaviour was not exercised.

---

## Reproduction scripts

The throwaway scripts the reviewers wrote live beside this file under
`.claude/bug-review-2026-10-16/<reviewer>/`, stored with a `.txt` suffix so the root-level
`ruff`, `mypy` and `pytest` gates do not treat them as shipped code. To run one, copy it back to
its real name outside the tree. The Python ones need the test builders on the path and the
repo's pytest config:

    PYTHONPATH=packages/hivemind/tests uv run python <script>.py
    cd packages/hivemind && PYTHONPATH=tests uv run pytest <test>.py -o asyncio_mode=auto --confcutdir=tests

The two Vitest files run from `packages/observation-web` with `pnpm vitest run <file>` once
copied under `tests/`.
