"""Test the Sandbox helpers: the path check and the capped output collector.

Fits into the Hive:
    Mirrors src/hivemind/royal_jelly/quarantine_comb/sandbox.py
    (codingrules section 3). Test infrastructure, not shipped.

Key invariants:
    - None: this module holds tests only.

See Also:
    - src/hivemind/royal_jelly/quarantine_comb/sandbox.py for the module under test.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hivemind.royal_jelly.quarantine_comb import SandboxPathError
from hivemind.royal_jelly.quarantine_comb.sandbox import CappedOutput, check_relative


@pytest.mark.parametrize("relative", ["pkg/tool.py", "report.json", "a/b/c.txt"])
def test_check_relative_accepts_plain_relative_paths(relative: str) -> None:
    assert check_relative(Path(relative)) == Path(relative)


@pytest.mark.parametrize("relative", ["", "/etc/passwd", "../x", "a/../../x", "a/.."])
def test_check_relative_refuses_absolute_empty_and_climbing_paths(relative: str) -> None:
    with pytest.raises(SandboxPathError):
        check_relative(Path(relative))


def test_capped_output_keeps_each_stream_and_counts_the_overflow() -> None:
    output = CappedOutput(cap=10)

    output.add(is_stderr=False, data=b"12345678")
    output.add(is_stderr=True, data=b"abcdef")
    output.add(is_stderr=False, data=b"zzz")
    result = output.result(exit_code=3, duration_s=-1.0)

    assert result.stdout == b"12345678"
    assert result.stderr == b"ab"
    assert output.dropped == 4 + 3
    assert result.exit_code == 3
    assert result.duration_s == 0.0
