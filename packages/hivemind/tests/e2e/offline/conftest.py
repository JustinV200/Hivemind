"""Provide the `egress` fixture: the no-egress socket guard, installed for one offline case.

Fits into the Hive:
    Test infrastructure (codingrules section 14.5), not shipped. Applies to the `test_offline_*`
    modules of this directory.

Key invariants:
    - The guard is installed before the test builds anything and removed after, by monkeypatch.
    - Teardown fails the test when any connect was refused, even if the code that dialled swallowed
      the `AssertionError` (an HTTP client turns it into "provider unavailable").

See Also:
    - tests/e2e/offline/guard.py for what the guard does.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from e2e.offline.guard import EgressLog, install_no_egress


@pytest.fixture
def egress(monkeypatch: pytest.MonkeyPatch) -> Iterator[EgressLog]:
    """Guard every socket connect; a case ends with `egress.assert_clean_and_live()`."""
    log = install_no_egress(monkeypatch)
    yield log
    assert not log.refused, f"egress to {log.refused}"
