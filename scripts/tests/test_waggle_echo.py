"""End-to-end test for scripts/waggle_echo.py: the phase 1 exit demo runs green.

Fits into the Hive:
    Layer: none (a test for a demo script). Runs the orchestrator in-process, which launches the
    server and client roles as real child interpreters on a loopback port, so what is tested is
    exactly what a developer sees from ``uv run --frozen python scripts/waggle_echo.py``. Marked
    slow (codingrules 14.2): two interpreter starts, a kill, a restart and the client's backoff
    add up to several seconds.

Key invariants:
    - None: this module holds tests, not behaviour.

See Also:
    - scripts/waggle_echo.py, the module under test.
"""

# codingrules 6.2: a sentence-shaped test name is its own docstring, and `assert` is how pytest
# reports failure. pyproject.toml's [tool.ruff.lint.per-file-ignores] grants S101/D103 to
# scripts/tests/**, so nothing is silenced here.

import pytest
import waggle_echo

# The milestones the summary must name, in the order the scenario reaches them; each is a row's
# event column, so a missing one means a step was skipped and a misordered one a regression.
EXPECTED_MILESTONES = [
    "ready",
    "pong",
    "task_accepted",
    "rejected waggle.signature.invalid",
    "tamper_rejected 1008",
    "waiting_for_outage",
    "killed",
    "link_lost",
    "queued 3",
    "ready",
    "reconnected",
    "replayed 3",
    "replay_answered 3",
    "exit 0",
    "replayed 3",
    "exit 0",
]


@pytest.mark.slow
def test_waggle_echo_reaches_every_milestone_and_exits_zero(
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code = waggle_echo.main([])

    captured = capsys.readouterr()
    assert exit_code == 0, captured.err
    assert "every milestone reached" in captured.out
    # The event column is the last on each row; comparing the columns in order checks the
    # sequence, not just membership.
    rows = [
        line for line in captured.out.splitlines() if line.startswith("  ") and "(s)" not in line
    ]
    assert [row.split(maxsplit=2)[2] for row in rows] == EXPECTED_MILESTONES
