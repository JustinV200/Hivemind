"""Test the runner's entry point: its argument check (the full run is in the end-to-end tests).

Fits into the Hive:
    Mirrors src/hivemind/royal_jelly/quarantine_comb/runner/main.py
    (codingrules section 3). Test infrastructure, not shipped.

Key invariants:
    - None: this module holds tests only.

See Also:
    - src/hivemind/royal_jelly/quarantine_comb/runner/main.py for the module under test.
"""

from __future__ import annotations

import pytest

from hivemind.royal_jelly.quarantine_comb.runner import main
from hivemind.royal_jelly.quarantine_comb.runner.main import EXIT_BAD_USAGE


@pytest.mark.parametrize("argv", [[], ["only-one"], ["a", "b", "c", "d"]])
def test_the_wrong_number_of_arguments_is_a_usage_error(
    argv: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(argv) == EXIT_BAD_USAGE
    assert "usage" in capsys.readouterr().err
