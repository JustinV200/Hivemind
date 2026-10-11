"""Read what an offline run left on the trail and judge it: every model call was local.

Phase 8's first exit criterion is "every `llm.call` on a local provider". The trail records the
provider's manifest name on each call (never a URL), so the judgement is two steps: collect the
`llm.call` events, then look every name up in the manifest's `[llm.providers]` and ask the
registry's own `runs_locally` whether that provider serves from this machine (in process, or a
loopback `base_url`). This module does both, plus the small reads the cases share: the trail of a
Hive that ran in another process (`hive run` writes the file; the test reads it afterwards), and
the `llm.call` events of one slot.

Fits into the Hive:
    Test infrastructure (codingrules section 14.5), not shipped. Used by the cases of
    `tests/e2e/offline/`. Calls into `hivemind.cli.stores` (`open_trail`, `provider_configs`),
    `hivemind.llm.registry` (`runs_locally`) and `hivemind.pheromone`.

Key invariants:
    - An empty set of calls never passes: a run that made no model call proved nothing.
    - A provider named on the trail but absent from the manifest fails, rather than being skipped.

See Also:
    - tests/e2e/offline/guard.py for the socket half of the same proof.
    - hivemind.llm.registry.runs_locally for the locality rule.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence

from hivemind.cli.stores import open_trail, provider_configs
from hivemind.forage.slots import ModelSlot
from hivemind.llm.registry import runs_locally
from hivemind.manifest import HiveManifest
from hivemind.pheromone import MAX_QUERY_LIMIT, LlmEvent, PheromoneEvent, TrailQuery

__all__ = ["assert_every_call_local", "calls_on", "llm_calls", "trail_of"]


def trail_of(manifest: HiveManifest) -> tuple[PheromoneEvent, ...]:
    """Read every event from the Hive's database file, from plain synchronous code.

    For a Hive that ran in `hive run`'s own event loop: its trail is on disk when it returns.
    """
    trail = open_trail(manifest.resolve_path(manifest.hive.db))
    return tuple(asyncio.run(trail.query(TrailQuery(limit=MAX_QUERY_LIMIT))))


def llm_calls(events: Sequence[PheromoneEvent]) -> list[LlmEvent]:
    """Every `llm.call` event in `events`, oldest first."""
    return [e for e in events if isinstance(e, LlmEvent) and e.kind == "llm.call"]


def calls_on(events: Sequence[PheromoneEvent], slot: ModelSlot) -> list[LlmEvent]:
    """Every `llm.call` event made on `slot`."""
    return [call for call in llm_calls(events) if call.slot == slot.value]


def assert_every_call_local(events: Sequence[PheromoneEvent], manifest: HiveManifest) -> None:
    """Assert each `llm.call` names a provider of `manifest` that serves from this machine.

    Args:
        events: The trail, or any part of it.
        manifest: The manifest the Hive ran under.

    Raises:
        AssertionError: There was no call, a call named no provider or one the manifest lacks, or
            a call's provider is hosted or on a non-loopback address.
    """
    calls = llm_calls(events)
    assert calls, "the run made no model call: nothing was proved"
    configs = provider_configs(manifest)
    for call in calls:
        assert call.provider is not None, f"llm.call on {call.slot} names no provider"
        assert call.provider in configs, f"llm.call names unknown provider {call.provider!r}"
        assert runs_locally(configs[call.provider]), (
            f"llm.call on {call.slot} went to {call.provider!r}, which does not run locally"
        )
