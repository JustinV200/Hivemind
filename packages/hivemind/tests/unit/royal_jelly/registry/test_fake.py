"""Tests for hivemind.royal_jelly.registry.fake: the in-memory registry's own corners.

The behaviour it shares with the SQLite store is in the contract suite; this module covers what
the contract does not reach: isolation between instances and the clock it stamps with.

Fits into the Hive:
    Mirrors src/hivemind/royal_jelly/registry/fake.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.royal_jelly.registry.fake for the module under test.
"""

from __future__ import annotations

from datetime import timedelta

from builders.royal_jelly import make_offer, make_tool_spec

from hivemind.royal_jelly.registry import InMemoryCombRegistry, QueenPrincipal
from waggle.clock import FakeClock
from waggle.messages.tool.authoring import ToolScope


async def test_two_fakes_do_not_share_rows() -> None:
    spec = make_tool_spec()
    report, offer = make_offer(spec)
    first, second = InMemoryCombRegistry(FakeClock()), InMemoryCombRegistry(FakeClock())

    await first.promote(spec, report, offer, QueenPrincipal(), "initial")

    assert await second.list_tools() == ()
    assert len(await first.list_tools()) == 1


async def test_writes_are_stamped_with_the_injected_clock() -> None:
    clock = FakeClock()
    registry = InMemoryCombRegistry(clock)
    spec = make_tool_spec()
    report, offer = make_offer(spec)
    clock.advance(60)

    change = await registry.promote(spec, report, offer, QueenPrincipal(), "initial")

    assert change.row.promoted_at == FakeClock().now() + timedelta(seconds=60)
    assert change.at == change.row.promoted_at
    assert (await registry.get(spec.name, ToolScope.HIVE, None)).promoted_at == change.at
