"""Tests for hivemind.royal_jelly.scaffold.fake: the scripted scaffolder the flow's tests use.

Fits into the Hive:
    Mirrors src/hivemind/royal_jelly/scaffold/fake.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.royal_jelly.scaffold.fake for the module under test.
"""

from __future__ import annotations

import pytest
from builders.royal_jelly import make_brief, make_scaffold_result

from hivemind.royal_jelly.errors import SpecRejectedError
from hivemind.royal_jelly.scaffold import FakeScaffolder


async def test_results_are_returned_in_order_and_the_last_repeats() -> None:
    brief = make_brief()
    first, second = make_scaffold_result(brief), make_scaffold_result(brief, version="1.0.1")
    fake = FakeScaffolder([first, second])

    results = [await fake.scaffold(brief) for _ in range(3)]

    assert results == [first, second, second]
    assert fake.briefs == [brief, brief, brief]


async def test_an_exception_entry_is_raised() -> None:
    fake = FakeScaffolder([SpecRejectedError("echo_tool", ("nope",))])

    with pytest.raises(SpecRejectedError):
        await fake.scaffold(make_brief())


async def test_a_callable_script_builds_the_result_from_the_brief() -> None:
    fake = FakeScaffolder(make_scaffold_result)
    brief = make_brief(name="other_tool")

    result = await fake.scaffold(brief)

    assert result.spec.name == "other_tool"
