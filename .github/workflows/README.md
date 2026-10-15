# CI workflows

The GitHub Actions workflows: `ci.yml` and `integration.yml` from roadmap step 0.4, then
`android.yml` and `pollen.yml`, which build release artifacts and run on tags and on demand.
`hygiene.yml` (roadmap step 0.3) has been retired: its job is now the `hygiene` job below, folded
into `ci.yml` so there is exactly one required workflow plus `integration.yml`.

## `ci.yml`

Runs on every push to `main` and every pull request. `lint`, `types`, `imports`, `hygiene`,
`tests` and `e2e` run on a Ubuntu + Windows matrix; `arch`, `audit` and `web` run once each.

| Job | Gate | Protects |
|---|---|---|
| `lint` | `ruff format --check`, `ruff check` | codingrules section 2 (ruff is format *and* lint) and section 5 (style/size rules ruff can see). |
| `types` | `mypy --strict` | codingrules section 2 ("zero errors on `main`"). |
| `imports` | `lint-imports` | codingrules section 4 (the layer table and its forbidden-import contracts). |
| `hygiene` | `check_sizes.py`, `check_no_model_ids.py`, `check_no_kind_branches.py`, `check_no_transcripts.py` | codingrules sections 5.1, 8.6, 8.7 and 8.8 respectively -- the four commit-time scripts from roadmap step 0.3, mirroring `.pre-commit-config.yaml`. |
| `tests` | `pytest -m "not integration and not e2e and not live_llm and not local_llm" --cov`, then `check_coverage_floors.py` | codingrules section 14 (the fast unit-test loop and its per-layer coverage floors). Coverage reports are uploaded per OS. |
| `e2e` | `pytest -m "e2e and not integration" packages/hivemind/tests/e2e`, ten minutes at most | codingrules 14.2's whole-Hive scenarios on every push. One that also needs Docker (phase 11's device containers) is marked `integration` too and runs in `integration.yml` instead. |
| `arch` | The same `lint`/`types`/`imports`/`hygiene`/`tests` commands, inside an `archlinux:latest` container | codingrules section 2's host table -- the Hive Stand runs on Arch Linux, so CI proves the gates pass there too, not only on the two GitHub-hosted OSes. |
| `audit` | `pip-audit` against the exported, non-workspace lockfile | codingrules section 15 ("a known-vulnerable pin blocks merge"). |
| `web` | `pnpm lint`, `pnpm format`, `pnpm typecheck`, `pnpm test`, `pnpm audit --audit-level=high` for `packages/observation-web/` | codingrules section 2's front-end toolchain row and section 15's `pnpm audit` requirement. |

## `integration.yml`

`workflow_dispatch` plus a nightly cron, Ubuntu only: runs `pytest -m "integration"`
(codingrules 14.2's Docker/SQLite/network-dependent tests) after checking a Docker daemon is
available. No integration test exists until phase 2; pytest's exit code 5 ("no tests collected")
is treated as success explicitly until then. Since phase 11 it also runs the whole-Hive scenarios
that start device containers (`tests/e2e/test_phase11_exit_*.py`, marked `e2e` and
`integration`), which build their own device image and take about half an hour.

## `android.yml`

Runs on tags `v*` and by hand (`workflow_dispatch`), never on a push, per ADR-0050: it builds the
web app, syncs it into the Capacitor project under `packages/observation-web/android/`, runs the
Gradle release build with the JDK the template targets and the Android SDK action, signs with the
release key from CI secrets, and uploads the APK as a workflow artifact for the phase 14 release.
The per-push gate for the front end stays the `web` job in `ci.yml`.

## `pollen.yml`

Roadmap step 11.7. Runs on tags `v*`, by hand (`workflow_dispatch`) and on a pull request that
touches `packages/pollen/**`, `packages/waggle/**`, `scripts/build_pollen.py`, the lock or the
workflow itself. One job on a five-leg matrix, each leg on its own platform because PyInstaller
cannot cross-compile: `ubuntu-22.04` (Linux x86-64), `ubuntu-24.04-arm` (Linux ARM64, the
Raspberry Pi), `windows-latest`, `macos-14` (Apple silicon) and `macos-15-intel` (x86-64; GitHub
retired `macos-13`, which the step was written against, in December 2025). Every leg runs the
pollen unit suite, builds the single-file `pollen` binary with `scripts/build_pollen.py`, runs
`pollen version` from it and uploads it as the artifact `pollen-<target>`.

The Linux legs build inside `python:3.12-bookworm` rather than on the runner. A binary links the
glibc of the machine that built it, and the runners are newer than Raspberry Pi OS (24.04 has glibc
2.39, a Pi's Debian 12 has 2.36). It is the full image, not `-slim`, because PyInstaller needs
`objdump` from binutils. The same legs then run the binary in a clean `debian:bookworm-slim` with
no Python: `pollen version`, `pollen install --data-dir ...` (no systemd there, so it must print
the command that starts the packet and succeed) and `pollen status` (not enrolled, so it must say
so and exit 1). `docs/runbooks/enrolling-a-device.md` says how an operator takes the artifact to a
device.
