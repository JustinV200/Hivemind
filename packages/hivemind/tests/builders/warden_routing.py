"""Wire a Warden's deps for routing without repeating the map, rows and rebind in every test.

A routing test needs the pieces `hivemind.wardens.spawn.route` reads off `WardenDeps`: a Forage map
(the Hive's list of models and their grade, price and free seats), the operator's `RoutingRules`,
the `[llm.slots]` rows that make up the worker slot's fallback chain, and a `rebind` that turns a
row's key into a live `BoundModel` (the manifest-order chain routing then reorders).
`routing_overrides` returns those as `make_warden_deps` overrides; the rows come from
`builders.routing.make_chain` (the first key is the slot's own `worker` row, each row's provider
and model are its name) and the sources from `builders.routing.make_chain_source`.

Fits into the Hive:
    Test infrastructure (codingrules section 14.5), not shipped. Used by the spawn and rebind tests
    under packages/hivemind/tests/unit/wardens.

Key invariants:
    - `rebind` is the production resolver (`resolve_key` over the same rows and map), so a test
      sees the same manifest-order chain a Warden's composition root hands it.

See Also:
    - tests/builders/routing.py for `make_chain` and `make_chain_source`.
    - tests/builders/wardens.py for `make_warden_deps`, whose overrides these are.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from builders.routing import make_chain

from hivemind.forage import ForageMap, ModelSlot, ModelSource
from hivemind.llm import FakeLLMProvider
from hivemind.llm.routing import RoutingRules
from hivemind.llm.slots import BoundModel, resolve_key
from waggle.clock import Clock

__all__ = ["routing_overrides"]


def routing_overrides(
    clock: Clock,
    sources: Sequence[ModelSource],
    keys: Sequence[str] = ("small", "strong"),
    *,
    rules: RoutingRules | None = None,
) -> dict[str, Any]:
    """Build the `make_warden_deps` overrides that give a Warden a map, rules and a chain.

    Args:
        clock: The clock the Warden's deps share; the map reads it for throttle expiry.
        sources: The Forage map's sources, named `src_<key>` by `make_chain_source`.
        keys: The worker chain's names, first is the slot's own `worker` row.
        rules: The routing rules; the shipped defaults when omitted.

    Returns:
        Overrides for `map`, `routing_rules`, `bindings` and `rebind`.
    """
    bindings = make_chain(*keys)
    forage_map = ForageMap(sources, clock)

    def rebind(key: str) -> BoundModel:
        # The worker slot, as the production composition root binds every sub-bee.
        return resolve_key(
            key, ModelSlot.WORKER, bindings, lambda name: FakeLLMProvider(name=name), forage_map
        )

    return {
        "map": forage_map,
        "routing_rules": rules if rules is not None else RoutingRules(),
        "bindings": bindings,
        "rebind": rebind,
    }
