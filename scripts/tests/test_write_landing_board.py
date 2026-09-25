"""Unit tests for scripts/write_landing_board.py.

Fits into the Hive:
    Layer: none (tests for a dev-time tool). Runs the script's `main` against temporary paths and
    the committed document.

Key invariants:
    - None: this module holds tests, not behaviour.

See Also:
    - scripts/write_landing_board.py, the module under test.
"""

from pathlib import Path

import write_landing_board

from hivemind.entrance.landing_board import render_document


def test_write_landing_board_check_passes_on_the_committed_document() -> None:
    assert write_landing_board.main(["--check"]) == 0


def test_write_landing_board_check_fails_on_a_drifted_document(tmp_path: Path) -> None:
    target = tmp_path / "openapi.json"
    target.write_text("{}\n", encoding="utf-8")

    assert write_landing_board.main(["--check", "--path", str(target)]) == 1
    assert target.read_text(encoding="utf-8") == "{}\n"


def test_write_landing_board_writes_a_missing_document(tmp_path: Path) -> None:
    target = tmp_path / "entrance" / "openapi.json"

    assert write_landing_board.main(["--path", str(target)]) == 0
    assert target.read_bytes() == render_document()
    assert write_landing_board.main(["--check", "--path", str(target)]) == 0
