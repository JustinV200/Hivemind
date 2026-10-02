"""Test composition root for the hivemind package.

This module is where hivemind's test suite wires together its collaborators — fakes for the
LLMProvider and CellBackend protocols, an in-memory Pheromone Trail, and similar test doubles —
once real modules exist here to test. Per codingrules section 8.2, the composition root for any
entry point (a CLI, a gateway, or a test suite) is exactly one file; for tests, that file is this
one, shared by tests/unit, tests/integration, tests/e2e, tests/contracts, tests/builders and
tests/evals.

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Loaded automatically by pytest before every test
    module under packages/hivemind/tests/. Nothing depends on it; it depends on structlog and
    `builders.orphan_browsers` only.

Key invariants:
    - A session starts by stopping the Chromium a killed earlier run orphaned (phase 6 handoff
      4.5 item 7), and only that: `builders.orphan_browsers` never touches a live run's browser.
    - Every test starts from structlog's own defaults: `_structlog_defaults` restores them after
      each test, since a test that runs the CLI configures logging for the whole process (WARNING
      and up, to standard error), and a later test capturing INFO events
      (`structlog.testing.capture_logs`) would otherwise see nothing.

See Also:
    - .claude/codingrules.md section 8.2 for the composition-root rule this file follows.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
import structlog
from builders.orphan_browsers import stop_orphaned_test_browsers


@pytest.fixture(scope="session", autouse=True)
def _no_orphaned_browsers() -> None:
    """Stop the browsers a killed earlier run left behind, before this run's first test.

    A session fixture, not a `pytest_sessionstart` hook: that hook reaches only the conftest files
    pytest loads before collection, which this one is not when a run names `packages`. Only
    orphans of a real browser harness are stopped, never a concurrent run's browsers
    (`builders.orphan_browsers` says how they are told apart); off Linux it finds nothing.
    """
    stop_orphaned_test_browsers()


@pytest.fixture(autouse=True)
def _structlog_defaults() -> Iterator[None]:
    """Give structlog its own defaults back once each test ends (module docstring)."""
    yield
    structlog.reset_defaults()
