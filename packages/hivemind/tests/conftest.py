"""Test composition root for the hivemind package.

This module is where hivemind's test suite wires together its collaborators — fakes for the
LLMProvider and CellBackend protocols, an in-memory Pheromone Trail, and similar test doubles —
once real modules exist here to test. Per codingrules section 8.2, the composition root for any
entry point (a CLI, a gateway, or a test suite) is exactly one file; for tests, that file is this
one, shared by tests/unit, tests/integration, tests/e2e, tests/contracts, tests/builders and
tests/evals.

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Loaded automatically by pytest before every test
    module under packages/hivemind/tests/. Nothing depends on it; it depends on nothing yet.

Key invariants:
    - None yet: no fixtures are defined until later roadmap steps populate the subsystems they
      would fake.

See Also:
    - .claude/codingrules.md section 8.2 for the composition-root rule this file follows.
"""
