"""Provide the `egress` fixture for the `local_llm` offline tests: the no-egress socket guard.

A real local server is on loopback too, so the same guard as the scripted e2e suite applies: any
connect to an address off loopback fails the test, and the test ends by asserting at least one
loopback connect was seen.

Fits into the Hive:
    Test infrastructure (codingrules section 14.5), not shipped.

Key invariants:
    - The guard is installed by monkeypatch and removed after the test.

See Also:
    - tests/e2e/offline/guard.py for the guard.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from e2e.offline.guard import EgressLog, install_no_egress


@pytest.fixture
def egress(monkeypatch: pytest.MonkeyPatch) -> Iterator[EgressLog]:
    """Guard every socket connect for the test; teardown fails it if any was refused."""
    log = install_no_egress(monkeypatch)
    yield log
    assert not log.refused, f"egress to {log.refused}"
