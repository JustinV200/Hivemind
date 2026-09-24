"""Unit tests for hivemind.supervision.capping.leave.table: LeavePolicyTable, load_leave_policy."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from hivemind.supervision.capping.errors import CappingError
from hivemind.supervision.capping.leave.model import LeaveVerdict
from hivemind.supervision.capping.leave.table import SingleVerdict, load_leave_policy


def test_load_leave_policy_reads_the_shipped_v0_table() -> None:
    table = load_leave_policy()

    assert table.general.max_home_bytes > 0
    assert table.classes.keep_root.verdict is LeaveVerdict.ALLOW
    assert table.classes.home.hive_stand_verdict is LeaveVerdict.ALLOW
    assert table.classes.home.borrowed_verdict is LeaveVerdict.ASK
    assert table.classes.startup.borrowed_verdict is LeaveVerdict.DENY
    assert table.classes.system.borrowed_verdict is LeaveVerdict.DENY
    assert table.classes.other.borrowed_verdict is LeaveVerdict.DENY
    assert table.classes.executable.verdict is LeaveVerdict.ASK


def test_single_verdict_is_frozen_and_forbids_extras() -> None:
    row = SingleVerdict(verdict=LeaveVerdict.ALLOW)

    with pytest.raises(ValidationError, match="frozen"):
        row.verdict = LeaveVerdict.DENY  # type: ignore[misc]  # The assignment is the test.
    with pytest.raises(ValidationError):
        row.model_validate({**row.model_dump(), "extra": "nope"})


def test_load_leave_policy_raises_for_a_missing_file(tmp_path: Path) -> None:
    with pytest.raises(CappingError, match="Could not read"):
        load_leave_policy(tmp_path / "does-not-exist.toml")


def test_load_leave_policy_raises_for_invalid_toml(tmp_path: Path) -> None:
    bad_file = tmp_path / "bad.toml"
    bad_file.write_text("this is not [ valid toml", encoding="utf-8")

    with pytest.raises(CappingError, match="Could not read"):
        load_leave_policy(bad_file)


def test_load_leave_policy_raises_for_a_missing_class_row(tmp_path: Path) -> None:
    bad_file = tmp_path / "incomplete.toml"
    bad_file.write_text("[general]\nmax_home_bytes = 100\n[classes]\n", encoding="utf-8")

    with pytest.raises(CappingError, match="invalid"):
        load_leave_policy(bad_file)


def test_load_leave_policy_raises_for_a_zero_size_threshold(tmp_path: Path) -> None:
    toml_file = tmp_path / "zero.toml"
    toml_file.write_text(
        "[general]\n"
        "max_home_bytes = 0\n"
        '[classes.keep_root]\nverdict = "ALLOW"\n'
        '[classes.executable]\nverdict = "ASK"\n'
        '[classes.startup]\nhive_stand_verdict = "ASK"\nborrowed_verdict = "DENY"\n'
        '[classes.system]\nhive_stand_verdict = "ASK"\nborrowed_verdict = "DENY"\n'
        '[classes.other]\nhive_stand_verdict = "ASK"\nborrowed_verdict = "DENY"\n'
        '[classes.home]\nhive_stand_verdict = "ALLOW"\nborrowed_verdict = "ASK"\n'
        'over_threshold_verdict = "ASK"\n',
        encoding="utf-8",
    )

    with pytest.raises(CappingError, match="invalid"):
        load_leave_policy(toml_file)
