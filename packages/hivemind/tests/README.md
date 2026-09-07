# hivemind tests

Tests for the hivemind package: the Queen (the Hive's central orchestrator) and every subsystem
she depends on. `conftest.py` here is the test composition root shared by every tree below it:
`unit/` (mirrors `src/hivemind/` one-to-one), `integration/` (real SQLite and real Docker, marked
and skippable), `e2e/` (whole-Hive scenarios), `contracts/` (protocol contract suites run against
every implementation), `builders/` (test-data builders) and `evals/` (handoff and model evaluation
harnesses). Each of those has its own README describing what it holds.
