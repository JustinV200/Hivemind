"""Define RipenerDeps: everything one ripening pass needs, bundled once by the composition root.

Ripening is the House Bee's (the maintenance Worker's) duty of turning Nectar (raw deposits in
the Honey Store, the Hive's knowledge base) into Honey (summarised, chunked, embedded, searchable
rows). Every stage of that pipeline -- summarising on the RIPENER model slot, embedding on the
EMBEDDER slot, deduplicating, indexing -- reads some of the same collaborators, so they travel
together in one frozen value rather than as a growing parameter list on every function
(codingrules 5.1). It lives in its own module, not beside `Ripener` in `pipeline.py`, because the
stages `pipeline.py` composes need it too and must not import the module that imports them.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy), inside `hivemind.honey_store.ripening`.
    Built by the composition root (`hivemind.cli.compose`) and by `hive honey ripen --now`; read
    by every module of this package. Calls into `hivemind.honey_store` (store, identity),
    `hivemind.llm` (bindings and gates) and `hivemind.manifest` only.

Key invariants:
    - A None `ripener` means no model summaries (the heuristic summary is used); a None
      `embedder` means no vectors (rows stay pending for a later pass and full-text search still
      finds them). Neither is ever an error.
    - A None gate means the unmetered direct gate; production wiring passes the Fanner's lanes so
      every ripening call is metered like any other model call (ADR-0036).

See Also:
    - hivemind.honey_store.ripening.pipeline for Ripener, the one consumer that runs a whole pass.
    - hivemind.manifest.schema.honey for HoneyRipeningSection, the `[honey.ripening]` settings.
    - docs/adr/0036-embedding-provider-and-reembedding-policy.md for the embedder's degrade rule.
"""

from __future__ import annotations

from dataclasses import dataclass

from hivemind.honey_store.identity import HoneyIdentity
from hivemind.honey_store.store import HoneyStore
from hivemind.llm import BoundEmbedder, BoundModel, CallGate, DirectEmbedGate, EmbedGate
from hivemind.manifest import HoneyRipeningSection
from waggle.clock import Clock

__all__ = ["RipenerDeps"]


@dataclass(frozen=True, slots=True)
class RipenerDeps:
    """The store, identity, clock, settings and model bindings one ripening pass runs on.

    Attributes:
        store: Where pending Nectar is read and Honey rows, vectors and events are written.
        identity: The Hive, node and actor stamped on every honey.* event the pass records.
        clock: The injected time source for every event.
        ripening: `[honey.ripening]`: pass sizes, chunking, summaries, embedding batches.
        ripener: The RIPENER binding that writes summaries; None writes heuristic ones.
        call_gate: How each summary call is made (the Fanner's lane); None calls directly.
        embedder: The EMBEDDER binding; None leaves every row without a vector for now.
        embed_gate: How each embedding call is made (the Fanner's lane); None calls directly.
    """

    store: HoneyStore
    identity: HoneyIdentity
    clock: Clock
    ripening: HoneyRipeningSection
    ripener: BoundModel | None = None
    call_gate: CallGate | None = None
    embedder: BoundEmbedder | None = None
    embed_gate: EmbedGate | None = None

    def embedding_gate(self) -> EmbedGate:
        """Return the gate every embedding call goes through.

        Returns:
            `embed_gate`, or an unmetered `DirectEmbedGate` when none was wired.
        """
        return self.embed_gate if self.embed_gate is not None else DirectEmbedGate()
