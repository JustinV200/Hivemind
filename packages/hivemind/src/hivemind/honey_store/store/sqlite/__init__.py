"""Provide SqliteHoneyStore: the durable HoneyStore, split into modules by table responsibility.

A concept needing more than one file becomes a package with an `__init__` face (codingrules 5.2);
this one holds the durable `HoneyStore` implementation, split into `store.py` (the class itself,
`create`, thin delegating methods), `nectar.py`/`honey.py`/`vectors.py` (one table each),
`sources.py` (`honey_nectar_sources`, a content duplicate's extra provenance, ADR-0033),
`search.py` (ranked reads over both `honey_fts` and `honey_vectors`), `stats.py` (aggregate
counts, watermarks, proposals), `lowering.py` (`honey_lowerings`, judge-reviewed label lowering,
ADR-0034) and `vec.py` (the sqlite-vec extension loader and codec), so each stays under the
codingrules 5.1 size limit and one file's responsibility is reviewable on its own.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy), inside the honey_store package.
    Constructed by a composition root; implements `hivemind.honey_store.store.protocol.HoneyStore`.
    Calls into `hivemind.common`, `hivemind.honey_store` (errors, models, schema, store.protocol)
    and `hivemind.pheromone` only.

Key invariants:
    - Every mutation runs one transaction on the store's own `ConnectionThread`; every method is
      serialised by the instance's own `asyncio.Lock` (codingrules section 11).

See Also:
    - .claude/codingrules.md section 5.2 for the package-over-prefixed-siblings rule this follows.
    - hivemind.honey_store.store.sqlite.store for SqliteHoneyStore itself.

Public API:
    - SqliteHoneyStore (store): the durable HoneyStore implementation.
"""

from hivemind.honey_store.store.sqlite.store import SqliteHoneyStore

__all__ = ["SqliteHoneyStore"]
