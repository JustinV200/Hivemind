"""Take raw Nectar into the Honey Store before it is ripened into anything queryable.

Nectar is unprocessed information a bee (a Worker or a Warden, the Hive's agents) or the Hive
itself brings back: a finding, a transcript, a tool result, a Handoff. This package is the intake
side of the Honey Store (the Hive's knowledge base): `NectarIntake` refuses an oversized deposit,
labels it with a clearance, files it under a scope, applies the Night Veil rule and stores it,
deduplicated, with its Pheromone Trail events; `ChunkGroups` reassembles deposits that arrive over
Waggle (the bee-to-bee wire protocol) in several chunks. A concept needing more than one file
becomes a package (codingrules 5.2): the chunk rules, the value shapes and the intake itself each
have their own module.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy), inside the honey_store package. Called
    by the Queen's tick (Waggle deposits) and in-process by the Queen and the House Bee (whole
    deposits); calls into the rest of `hivemind.honey_store` (clearance, scope, identity, errors,
    models, store), never into its `ripening` or `honey` sub-packages.

Key invariants:
    - Every deposit reaches the store through `NectarIntake`; nothing else builds a NectarDraft
      for a real deposit.
    - No trail event of any kind is recorded for a Night Veil source (ADR-0035).

See Also:
    - docs/adr/0035-honey-store-sqlite-fts5-sqlite-vec.md for the intake rules.
    - docs/waggle/spec.md sections 5 and 8.7 for the chunking rules and NectarDeposit.
    - hivemind.honey_store.ripening for the House Bee's pipeline that consumes stored Nectar.

Public API:
    - NectarIntake (intake): `submit` a whole deposit, `receive_chunk` a Waggle chunk,
      `expire_groups` from the Queen's housekeeping; SHA256_PREFIX_CHARS, the digest prefix a
      rejection event carries.
    - NectarSubmission, DepositSource, IntakeResult (submission): what intake takes and returns;
      submission_from_deposit, handoff_source_key and HANDOFF_SOURCE_KEY_PREFIX, the shared
      `handoff:<event id>` dedupe key a Handoff carries whichever way it arrives.
    - ChunkGroups, MAX_OPEN_CHUNK_GROUPS, CHUNK_GROUP_TIMEOUT_S (reassembly): chunked-deposit
      reassembly under docs/waggle/spec.md section 5.
"""

from hivemind.honey_store.nectar.intake import SHA256_PREFIX_CHARS, NectarIntake
from hivemind.honey_store.nectar.reassembly import (
    CHUNK_GROUP_TIMEOUT_S,
    MAX_OPEN_CHUNK_GROUPS,
    ChunkGroups,
)
from hivemind.honey_store.nectar.submission import (
    HANDOFF_SOURCE_KEY_PREFIX,
    DepositSource,
    IntakeResult,
    NectarSubmission,
    handoff_source_key,
    submission_from_deposit,
)

__all__ = [
    "CHUNK_GROUP_TIMEOUT_S",
    "HANDOFF_SOURCE_KEY_PREFIX",
    "MAX_OPEN_CHUNK_GROUPS",
    "SHA256_PREFIX_CHARS",
    "ChunkGroups",
    "DepositSource",
    "IntakeResult",
    "NectarIntake",
    "NectarSubmission",
    "handoff_source_key",
    "submission_from_deposit",
]
