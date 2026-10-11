"""Build routing inputs without repeating chain and source boilerplate in every test.

A routing test needs a slot's fallback chain (`[llm.slots]` rows that name each other), the Forage
map sources those rows name, and the rest of `RoutingInputs`. `make_chain` builds the rows for the
`worker` slot from a list of keys, where each row's provider and model are its own key;
`make_chain_source` builds the matching map source (its id is `src_<key>`); `make_routing_inputs`
assembles them into a `RoutingInputs` with the default rules, NORMAL tempo and no Cell.

Fits into the Hive:
    Test infrastructure (codingrules section 14.5), not shipped. Used by the tests under
    packages/hivemind/tests/unit/llm/routing.

Key invariants:
    - Every builder returns a real, validated value; overrides replace defaults, never bypass
      validation.

See Also:
    - tests/builders/forage.py for `make_source`, which `make_chain_source` wraps.
    - tests/builders/llm.py for `make_binding`, which `make_chain` wraps.
"""

from __future__ import annotations

from collections.abc import Sequence

from builders.forage import make_source
from builders.llm import make_binding

from hivemind.forage import ModelSlot, ModelSource, SlotBinding, Tempo
from hivemind.llm.routing import RoutingInputs, RoutingRules

__all__ = ["make_chain", "make_chain_source", "make_routing_inputs"]


def make_chain(*keys: str) -> tuple[SlotBinding, ...]:
    """Build `[llm.slots]` rows for the worker slot: `keys[0]` falls back to `keys[1]`, and so on.

    Args:
        *keys: The chain's keys in fallback order; the first is renamed `worker`, the slot's own.

    Returns:
        One row per key; each row's provider and model are the name given, and the first row's
        own key is `worker`.
    """
    rows: list[SlotBinding] = []
    for index, name in enumerate(keys):
        key = ModelSlot.WORKER.manifest_key if index == 0 else name
        fallback = keys[index + 1] if index + 1 < len(keys) else None
        rows.append(make_binding(key=key, provider=name, model=name, fallback=fallback))
    return tuple(rows)


def make_chain_source(name: str, **overrides: object) -> ModelSource:
    """Build the map source for the chain row whose provider and model are `name`.

    Args:
        name: The row's provider and model; the source id is `src_<name>`.
        **overrides: `make_source` overrides (grade, cost, seats, abundance, distance, ...).
    """
    return make_source(source_id=f"src_{name}", provider=name, model=name, **overrides)


def make_routing_inputs(
    sources: Sequence[ModelSource], keys: Sequence[str] = ("a", "b"), **overrides: object
) -> RoutingInputs:
    """Build RoutingInputs for the worker slot over `keys` and `sources`.

    Args:
        sources: The Forage map's sources.
        keys: The chain's names, first is the slot's own row (see `make_chain`).
        **overrides: `RoutingInputs` fields that replace the defaults (tempo, rules, cell_id,
            hosting, required_capabilities, local_providers, slot, bindings).
    """
    fields: dict[str, object] = {
        "slot": ModelSlot.WORKER,
        "tempo": Tempo(),
        "required_capabilities": frozenset(),
        "sources": tuple(sources),
        "bindings": make_chain(*keys),
        "rules": RoutingRules(),
    }
    fields.update(overrides)
    return RoutingInputs(**fields)  # type: ignore[arg-type]
