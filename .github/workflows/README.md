# CI workflows

Two GitHub Actions workflows, per roadmap step 0.4. `hygiene.yml` (roadmap step 0.3) has been
retired: its job is now the `hygiene` job below, folded into `ci.yml` so there is exactly one
required workflow plus `integration.yml`.

## `ci.yml`

Runs on every push to `main` and every pull request. `lint`, `types`, `imports`, `hygiene` and
`tests` run on a Ubuntu + Windows matrix; `arch`, `audit` and `web` run once each.

| Job | Gate | Protects |
|---|---|---|
| `lint` | `ruff format --check`, `ruff check` | codingrules section 2 (ruff is format *and* lint) and section 5 (style/size rules ruff can see). |
| `types` | `mypy --strict` | codingrules section 2 ("zero errors on `main`"). |
| `imports` | `lint-imports` | codingrules section 4 (the layer table and its forbidden-import contracts). |
| `hygiene` | `check_sizes.py`, `check_no_model_ids.py`, `check_no_kind_branches.py`, `check_no_transcripts.py` | codingrules sections 5.1, 8.6, 8.7 and 8.8 respectively -- the four commit-time scripts from roadmap step 0.3, mirroring `.pre-commit-config.yaml`. |
| `tests` | `pytest -m "not integration and not e2e and not live_llm and not local_llm" --cov`, then `check_coverage_floors.py` | codingrules section 14 (the fast unit-test loop and its per-layer coverage floors). Coverage reports are uploaded per OS. |
| `arch` | The same `lint`/`types`/`imports`/`hygiene`/`tests` commands, inside an `archlinux:latest` container | codingrules section 2's host table -- the Hive Stand runs on Arch Linux, so CI proves the gates pass there too, not only on the two GitHub-hosted OSes. |
| `audit` | `pip-audit` against the exported, non-workspace lockfile | codingrules section 15 ("a known-vulnerable pin blocks merge"). |
| `web` | `pnpm lint`, `pnpm format`, `pnpm typecheck`, `pnpm test`, `pnpm audit --audit-level=high` for `packages/observation-web/` | codingrules section 2's front-end toolchain row and section 15's `pnpm audit` requirement. |

## `integration.yml`

`workflow_dispatch` plus a nightly cron, Ubuntu only: runs `pytest -m "integration"`
(codingrules 14.2's Docker/SQLite/network-dependent tests) after checking a Docker daemon is
available. No integration test exists until phase 2; pytest's exit code 5 ("no tests collected")
is treated as success explicitly until then.
