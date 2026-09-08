# scripts

Development and operations helpers, one job per script, each starting with a header comment
saying what it does and how to run it. The roadmap adds more as each phase needs one (image
builds, migration runners, and similar). Hygiene checkers landed in step 0.3, run as
`uv run --frozen python scripts/<name>.py [paths...]` (default: the whole repo), wired into
`.pre-commit-config.yaml` and `.github/workflows/hygiene.yml`:

- `check_sizes.py` -- fails a `.py`/`.ts`/`.tsx` file that breaks the codingrules 5.1 size limits
  (file, function/method, class length, parameter count); delegates TS/TSX function-length
  estimation to `size_rules_ts.py`.
- `check_no_model_ids.py` -- fails on a model id or provider URL literal outside `manifest/` and
  `docs/` (codingrules 8.6).
- `check_no_kind_branches.py` -- fails on a `cell.kind` branch outside placement and the
  Undertaker (codingrules 8.7).
- `check_no_transcripts.py` -- fails on a `messages`/`history` attribute that accumulates state
  outside `hivemind/memory/` (codingrules 8.8).
- `check_coverage_floors.py` -- fails when a package's test coverage is below its codingrules
  14.1 floor; reads an existing `coverage.json`, so it belongs to CI, not pre-commit.
- `waggle_echo.py` -- the phase 1 exit demo: starts a Waggle WebSocket server and a client as
  two child processes with per-node Ed25519 keys, has them exchange `Ping`/`Pong` and a signed
  `TaskAssign`, refuse a tampered envelope, and replay the client's outbox after the server is
  killed and restarted on the same port; prints a milestone table and exits 0 only when every
  step happened in order. Run as `uv run --frozen python scripts/waggle_echo.py`; the roles and
  key layout live in `waggle_echo_server.py`, `waggle_echo_client.py` and `waggle_echo_keys.py`.

Tests for all of the above live under `scripts/tests/`.
