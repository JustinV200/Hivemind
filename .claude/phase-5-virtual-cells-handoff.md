# Phase 5 handoff (Virtual Cells and placement): state, map, merge plan, open items

> For an agent starting with a clean context. Roadmap steps 5.1 to 5.13, 5.3a, 5.7a and 5.7b were
> implemented on 2026-09-21 on branch `feat/phase-5-virtual-cells`, by an orchestrator dispatching
> one subagent per step or pair of steps, in a separate git worktree while another session built
> Leavings (5.0a to 5.0e) on `feat/phase-5-leavings` in the main checkout. Neither branch has been
> merged into the other yet; section 3 is the merge plan.

Read first: `CLAUDE.md`, `.claude/codingrules.md` sections 4, 5, 8.7, 8.12, 12, 17 and Appendix C,
`.claude/roadmap.md` phase 5, and ADRs `docs/adr/0026-*` to `0030-*`. The Leavings branch owns
ADR-0025.

## 1. Where things stand

- Branch `feat/phase-5-virtual-cells`, cut from `main` at `2563154`, in the worktree
  `C:\Justin\Projects\HiveMind\Hivemind-virtual-cells`. One commit per step or step pair; not
  pushed.
- Every box 5.1 to 5.13 plus 5.3a, 5.7a, 5.7b is ticked. 5.0a to 5.0e are the other branch's.
- Gates at handover, whole repo, all green: `ruff format --check`, `ruff check`, `mypy`,
  `lint-imports` (9 contracts), the five `scripts/check_*.py`, and
  `pytest -m "not integration and not live_llm and not local_llm" packages scripts/tests`
  (5760 passed, e2e included, about two minutes on a quiet machine).
- Docker Desktop and QEMU are **not installed** on the development host. Every backend is proven
  against fakes and contract suites; the marked `integration` tests skip. The Docker and QEMU
  SDK/process code has never touched a real daemon. Section 5 lists what that leaves unproven.
- Line endings: working copy CRLF, git normalises to LF; ignore the warnings.
- Run `uv` with `env -u VIRTUAL_ENV uv ...` from this worktree: a stale `VIRTUAL_ENV` from the
  main checkout once made `uv pip install` land packages in the wrong venv (`docker`, `pywin32` in
  `Hivemind/.venv`; harmless, pruned by the next `uv sync` there).

## 2. What phase 5 implemented (map)

| Step | Modules | Notes |
|---|---|---|
| 5.1 | `hive/models.py`, `hive/cell_state.py`, `hive/errors.py` | `VirtualCellSpec` (image, cpu, memory, disk, lifetime, `NetworkPolicy` NONE/EGRESS_ONLY/ALLOWLIST/VPN_TOR, exoskeleton, capacity, comb_shield, ready_timeout, hive_id, labels); `VirtualCellStatus` PROVISIONING→READY→GRANTED→RELEASED→DORMANT/DESTROYING→DESTROYED, FAILED; NIGHT_VEIL never DORMANT. Appendix C row updated to match. |
| 5.2 | `hive/backends/base.py`, `hive/registry.py`, `hive/backends/fake.py` | `CellBackend` protocol (provision, destroy, list_cells by label, pause, resume, `BackendCapabilities`), `BackendRegistry`; contract suite `tests/contracts/test_cell_backend_contract.py` over fake, docker, qemu, cloud harnesses. |
| 5.3, 5.5 | `images/base-ubuntu/`, `cell/in_cell.py`, `cell/local/process.py`, `wardens/spawn/in_cell.py`, `cli/in_cell/` | Entry point `hivemind-in-cell`: reads `HIVEMIND_*` (in `manifest/env.py`), dials out, signed `CellReady` → `CapacityReport` → heartbeats, then runs a real `Warden` over `InCellSpawnSource`; `Shutdown`/`CellTeardownRequest` stop it; trail segments ship via `TrailSegmentSync` (`wardens/trail_sync.py`, received in `queen/trail_sync.py`). Model access inside a Cell is a fake provider until grants carry base URLs (phase 8); `config.rewrite_loopback_base_url` is ready. |
| 5.4 | `hive/backends/bootstrap.py`, `hive/backends/docker/` | `CellBootstrap` (per-Cell keypair, env), `QueenEndpoint`, `ReadinessGate` seam; `DockerClientPort` with SDK and fake implementations, `DockerCellBackend`. `disk_bytes` not enforced (storage driver); NONE is best-effort on Docker Desktop; ALLOWLIST is a label until the in-Cell firewall. Optional extra `hivemind[docker]`. |
| 5.6 | `hive/lifecycle.py`, `queen/cell_gate/{gate,listener,provider,release,snapshot}.py`, `queen/attach.py`, `waggle/uris.py` | `CellLifecycle` owns every edge and backend call; `QueenReadinessGate`, `CellListener` (WebSocket server, attaches a `WardenLink` on CellReady + heartbeat), `LifecycleVirtualCellProvider` (`VirtualCellProvider` for the dispatcher), `make_on_task_finished` (release → overwinter or teardown). `check_waggle_uri(..., allow_virtual_cell_gateway_host=True)` accepts `ws://` to `host.docker.internal`/RFC1918. |
| 5.7 | `queen/placement/{decide,rules,models,inventory,policy}.py`, `queen/dispatcher/` (package), `manifest/schema/placement.py` | Pure `decide(needs, inventory, forage, policy)` → `ReuseReal | ReuseDormant | ProvisionVirtual` with a reason; hard rules before `prefer`; `[placement]` and `[virtual_cells]` sections. A footprint no Cell can bear now fails placement instead of sending a zero-bee grant (closes the phase 4 open item). |
| 5.7a | `queen/placement/policy.py`, `queen/forage/night_veil.py`, `cell/needs.py` (`RequestOrigin`), `brood_chamber/task/model.py` (`TaskSpec.origin`) | `NightVeilConstraints`, `check_night_veil`, `night_veil_local_only`, `restrict_to_local`. |
| 5.7b | `hive/night_veil/` | `NightVeilProbe` (13 checks), pure `attest`, `SessionNightVeilProbe`, `FakeNightVeilProbe`, `attest_cell` recording one `cell.attested`. Hooked in `provider._acquire_provision` for NIGHT_VEIL specs. |
| 5.3a | `images/night-veil-ubuntu/` | Dockerfile, nftables kill-switch, torrc, systemd units, README. `FROM base-ubuntu` with `TODO(6.1)` to rebase on `desktop-ubuntu`. Needs systemd as PID 1, so QEMU is its realistic backend today. |
| 5.8 | `workers/roles/undertaker/` | `Undertaker` (destroy_virtual, release_real, retry with backoff), `sweep_orphans` from labels and the trail (the phase 4 orphan-lease sweep), injected `GrantRevoker`, `WaxRetirer`, `LeavingsRemover` seams. |
| 5.9 | `hive/overwinter/{policy,pool}.py` | Pure `decide_release` (ADR-0029 rules), `OverwinterPool` as bookkeeping/selection only. |
| 5.10 | `hive/snapshot/`, `wardens/snapshot_relay.py`, `queen/cell_gate/snapshot.py` | `DockerSnapshotter` (commit + recreate), `QemuSnapshotter` (savevm/loadvm), `SnapshotLedger` and `SqliteSnapshotLedger`; the in-Cell Warden snapshots through `RelaySnapshotter` → `CellSnapshotRequest`/`Reply` (waggle 1.5) → the Queen's backend. The Capping gate needed no change. |
| 5.11 | `hive/backends/qemu/`, `scripts/build_cell_image.py`, `images/base-ubuntu/vm/` | `QemuRunnerPort`, `ProcessQemuRunner` (user-mode networking, cloud-init NoCloud seed, serial readiness marker, QMP over Unix socket or loopback TCP by probe), `FakeQemuRunner`. The image build script refuses until a real Ubuntu cloud-image digest is supplied (`--sha256` or `HIVEMIND_QEMU_BASE_IMAGE_SHA256`). |
| 5.12 | `hive/backends/cloud/` | `CloudCredentials`, `PricingTag` → Forage cost, `CloudCellBackend` protocol, `FakeCloudCellBackend` as the reference; no vendor SDK. |
| 5.13 | `cli/readback/{virtual,virtual_offline,virtual_abscond}.py`, `queen/cluster/` | `hive cells inspect/destroy/release/snapshot/rollback/abscond`, offline from labels and the trail; `release` is a RELEASE order the running Queen turns into `Intervene(RELEASE_LEASE)`. |
| Waggle | `waggle/messages/cell/snapshot.py`, `oversight.py`, `docs/waggle/spec.md` | Version 1.5: four snapshot messages and `InterventionAction.RELEASE_LEASE`. 1.3 and 1.4 belong to the Leavings branch. |
| Trail | `pheromone/events/families.py` | New `cell.*` kinds appended at the end of the cell block: provisioning, granted, virtual_released, resumed, destroying, provision_failed, evicted, orphans_swept. |

## 3. Merge plan with `feat/phase-5-leavings`

Agreed with the Leavings session during the build (both sides kept to it):

- Disjoint file ownership except: `pheromone/events/families.py` (both append to the cell block:
  theirs `cell.left`, `cell.leaving_removed`; ours a block at the end), `cli/readback/cells.py`
  (both add one nesting line), `hive/__init__.py` (ours only), `queen/deps.py` (both additive:
  theirs `scratch_root`, `keep_root`; ours placement/virtual fields), `manifest/schema/core.py`
  (theirs only), `docs/adr/README.md` (their 0025 row, then our 0026-0030).
- Waggle: they took 1.3 and 1.4, we took 1.5. After merge, `envelope.py`'s version must read 1.5
  and the spec's catalogue table must list all three bumps' messages; the drift test will say.
- Migrations: they added `cell/leavings/migrations/0001`; we added `hive/snapshot/0001` and
  `queen/cluster/0002_add_release_lease_id.sql`. No Brood Chamber migration on either side.
- `supervision/capping/gate.py` became a package on their branch. We changed nothing in it.
- Constructor changes on their side that our composition roots will meet: `HiveStandSource`
  takes a `leavings` store, `HiveStandLeaseReleaser(clock, leavings, identity, *, keep_scratch)`,
  `QueenDeps.scratch_root`/`keep_root`, `Queen.submit_goal` body moved to
  `queen/goal_submission.py`, `complete_structured(options=LadderOptions(...))`. Expect
  `cli/compose/deps.py`, `cli/in_cell/deps.py` and the test builders to need those arguments.
- Their `RealCellLease.note_allowed_path()` widens allowed paths in place and
  `LocalProcessSession` reads `lease.allowed_paths` live. `InCellSession` shares
  `cell/local/process.py`; check it also reads live after merge.
- Undertaker seam to wire after merge: `workers/roles/undertaker/role.py` takes a
  `LeavingsRemover` (`mark_cell_removed(cell_id, at) -> int`); the adapter over
  `hivemind.cell.leavings.LeavingsStore` is `list_leavings(cell_id)` then `mark_removed(cell_id,
  path, removed_at, event)` per row with a `cell.leaving_removed` event each.
- Their leave policy reads `Cell.source == "hive_stand"` for `is_hive_stand`; a Virtual Cell
  takes the "borrowed" rows of `leave-policy.toml`. Add a row if a disposable Cell should be
  more permissive.

## 4. Testing state and the e2e slice

- Unit and contract suites cover every module; the backend contract suite runs over four
  harnesses; the CellSession contract over local, in-cell and fake.
- `tests/e2e/test_virtual_cells.py` and `test_virtual_cells_night_veil.py` prove the phase 5
  exit criteria against the fake backend with a real in-Cell Warden run in-process per Cell:
  prefer=virtual (three Cells, each with its own attached Warden, overwinter and teardown
  variants), prefer=real (zero Cells), isolation=required, dormant reuse, provision failure with
  the retry-once path, a BLOCK wax on the Hive Stand and its clearing, abscond leaving zero Cells
  and the host left as found, and Night Veil attestation green/red at the provider level. Twelve
  scenarios, ~20 s, no xfails. The Leavings bullet is proven on the other branch.
- Writing that suite found four production defects, all fixed in commit `bad9fe5`: a
  double-dispatch race between `Queen.submit_goal` and the tick loop while a provision was in
  flight (now a per-Queen `dispatch_lock`); the lifecycle's READY -> GRANTED edge was never driven
  by the dispatcher (now `QueenDeps.on_cell_granted`); `[placement]` was never wired into
  `QueenDeps` by the composition root; and `common.tasks.reap` swallowed the reaping task's own
  cancellation, so a Warden whose Queen link had closed could never be cancelled.
- Real-run traps carried over from phase 4 still apply (`--timeout 900`, LM Studio context 16384,
  never run the e2e suite during a real `hive run`).

## 5. Open items, in suggested order

1. **Install Docker Desktop and run `pytest -m integration`.** `tests/integration/test_docker_backend.py`
   provisions the real `images/base-ubuntu` image against a loopback Waggle server. The
   integration workflow must also `uv sync --extra docker` for the SDK to be present.
2. **Provider base URLs inside a Cell.** Grants name a provider binding, not a URL, so the in-Cell
   provider registry is a fake (`cli/in_cell/providers.py`). Phase 8's hosting plan should carry
   Cell-relative URLs; `rewrite_loopback_base_url` is the interim helper.
3. **Night Veil end to end.** `PlacementPolicy.night_veil` is never built from the manifest, and
   the production probe factory in `cli/compose/virtual_cells.py` fails closed because the Queen
   has no `CellSession` to a Virtual Cell (only a Waggle link). Options: run the probe inside the
   Cell before `CellReady` and carry the result on `CellReady` (a waggle field), or a session
   relay. Also `_build_docker`/`_build_qemu` build one `QueenEndpoint` per backend, so
   `socks_proxy_url` is not yet set per Night Veil Cell.
4. **`RealCellLease` cannot retry a failed release**: `release()` moves to `RELEASING` before
   calling the releaser and `lease_state.py` has no edge back, so the Undertaker calls it once
   (`TODO(merge)` in `undertaker/role.py`). Add `RELEASING → LEASED` or a `RELEASE_FAILED` state
   on `cell/lease_state.py` after the merge (that file is the Leavings branch's).
5. **Queen signing key is minted per process** in `cli/compose/virtual_cells.py`; a Cell that
   outlives a Queen restart cannot verify the new Queen. Needs a manifest field or key file.
6. **`max_sub_bees` for Virtual specs is hardcoded to 4** in `cli/compose/virtual_cells.py`.
7. **Snapshot relay matches replies FIFO per kind**, not by request id; fine for one in-flight
   proposal per Warden, wrong under concurrency. Carry the envelope `correlation_id` into
   `InboxItem` to fix.
8. **Overwinter scrub is a no-op** (`_null_scrub`): the Warden inside the Cell should remove
   scratch and stop sub-bees on `Intervene(RELEASE_LEASE)` before the pool pauses it; today the
   pause happens with whatever is left.
9. **`night-veil-ubuntu` under Docker**: needs systemd; today's Docker hardening
   (`cap_drop ALL`, read-only root) forbids it. Either a Night-Veil-specific container profile
   or QEMU only.
10. `build_cell_image.py` has never run; supply the Ubuntu 24.04 cloud-image SHA256 and try it
    on a host with `qemu-img` and `qemu-system-x86_64`.
