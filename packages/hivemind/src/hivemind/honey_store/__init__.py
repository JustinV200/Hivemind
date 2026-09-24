"""Define the Honey Store, the Hive's cold-tier knowledge base.

Raw Nectar (unprocessed captured information) is taken in, ripened through a pipeline into Honey
(retrievable, labelled knowledge), and retrieved by Workers before they act. This face re-exports
roadmap 7.2/7.3's models, clearance and scope rules, schema and SQLite store (everything but
intake, ripening, retrieval and the browser, which land in later dispatches' `nectar/`,
`ripening/`, `honey/` and `browse.py`) so a caller writes `from hivemind.honey_store import
SqliteHoneyStore` without knowing the split (codingrules 5.2).

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy). Called by workers before they act
    (retrieval, a later dispatch) and as they capture Nectar (intake, a later dispatch). May
    import `hivemind.cell`, `hivemind.guard`, `hivemind.llm`, `hivemind.manifest`,
    `hivemind.pheromone`, `hivemind.forage` and `hivemind.common`; never `hivemind.memory`,
    `hivemind.supervision` or `hivemind.brood_chamber` (independent Layer-2 siblings,
    `lint-imports` enforces it).

Key invariants:
    - Whatever is not re-exported here is private to this package (codingrules 5.4).
    - `nectar/`, `ripening/`, `honey/` (sub-packages) and `browse.py` still carry no public names;
      later dispatches populate them.

See Also:
    - .claude/codingrules.md section 4 for the layer 2 row this package occupies.
    - docs/adr/0031-honey-store-sqlite-fts5-sqlite-vec.md and docs/adr/0032-embedding-provider-
      and-reembedding-policy.md for the decisions this package implements.
    - .claude/roadmap.md phase 7 for the work that populates this package end to end.

Public API:
    - HoneyStoreError, NectarNotFoundError, HoneyNotFoundError, NectarRejectedError and its seven
      concrete reasons, LabelLoweringError, InvalidScopeError (errors): this package's error tree.
    - HIVE_SCOPE, NectarProvenance, cell_scope, task_scope, bee_scope, folder_for_scope,
      scope_for_folder, honey_ref, parse_honey_ref, scope_for_nectar, queen_read_capabilities,
      warden_read_capabilities, worker_read_capabilities, readable_globs, is_readable (scope):
      scope strings, folders and the default `honey:read` capability sets.
    - LabelApprover, intake_floor, intake_label, raise_label, reader_ceiling, check_lowering
      (clearance): the pure label rules.
    - Nectar, NectarDraft, NectarOrigin, NectarState, Honey, HoneyDraft, HoneyPart, ReadFilter,
      TextCandidate, VectorCandidate, HoneyStats (models): the value models the store persists.
    - apply_honey_store_migrations, MIGRATIONS_PACKAGE, SUBSYSTEM (schema): this subsystem's
      numbered migration series.
    - HoneyStore, NectarAdded, HoneyProposal, build_match, MAX_MATCH_TOKENS, SqliteHoneyStore
      (store): the persistence protocol, its SQL builder and its durable implementation.
"""

from hivemind.honey_store.clearance import (
    LabelApprover,
    check_lowering,
    intake_floor,
    intake_label,
    raise_label,
    reader_ceiling,
)
from hivemind.honey_store.errors import (
    DepositTimedOutError,
    FirstChunkNotAtZeroError,
    HoneyNotFoundError,
    HoneyStoreError,
    InvalidScopeError,
    LabelLoweringError,
    NectarNotFoundError,
    NectarNotRipenableError,
    NectarRejectedError,
    NectarTooLargeError,
    NightVeilRefusedError,
    OffsetMismatchError,
    Sha256MismatchError,
    TooManyOpenDepositsError,
)
from hivemind.honey_store.models import (
    Honey,
    HoneyDraft,
    HoneyPart,
    HoneyStats,
    Nectar,
    NectarDraft,
    NectarOrigin,
    NectarState,
    ReadFilter,
    TextCandidate,
    VectorCandidate,
)
from hivemind.honey_store.schema import MIGRATIONS_PACKAGE, SUBSYSTEM, apply_honey_store_migrations
from hivemind.honey_store.scope import (
    HIVE_SCOPE,
    NectarProvenance,
    bee_scope,
    cell_scope,
    folder_for_scope,
    honey_ref,
    is_readable,
    parse_honey_ref,
    queen_read_capabilities,
    readable_globs,
    scope_for_folder,
    scope_for_nectar,
    task_scope,
    warden_read_capabilities,
    worker_read_capabilities,
)
from hivemind.honey_store.store import (
    MAX_MATCH_TOKENS,
    HoneyProposal,
    HoneyStore,
    NectarAdded,
    NectarEvents,
    SqliteHoneyStore,
    build_match,
)

__all__ = [
    "HIVE_SCOPE",
    "MAX_MATCH_TOKENS",
    "MIGRATIONS_PACKAGE",
    "SUBSYSTEM",
    "DepositTimedOutError",
    "FirstChunkNotAtZeroError",
    "Honey",
    "HoneyDraft",
    "HoneyNotFoundError",
    "HoneyPart",
    "HoneyProposal",
    "HoneyStats",
    "HoneyStore",
    "HoneyStoreError",
    "InvalidScopeError",
    "LabelApprover",
    "LabelLoweringError",
    "Nectar",
    "NectarAdded",
    "NectarDraft",
    "NectarEvents",
    "NectarNotFoundError",
    "NectarNotRipenableError",
    "NectarOrigin",
    "NectarProvenance",
    "NectarRejectedError",
    "NectarState",
    "NectarTooLargeError",
    "NightVeilRefusedError",
    "OffsetMismatchError",
    "ReadFilter",
    "Sha256MismatchError",
    "SqliteHoneyStore",
    "TextCandidate",
    "TooManyOpenDepositsError",
    "VectorCandidate",
    "apply_honey_store_migrations",
    "bee_scope",
    "build_match",
    "cell_scope",
    "check_lowering",
    "folder_for_scope",
    "honey_ref",
    "intake_floor",
    "intake_label",
    "is_readable",
    "parse_honey_ref",
    "queen_read_capabilities",
    "raise_label",
    "readable_globs",
    "reader_ceiling",
    "scope_for_folder",
    "scope_for_nectar",
    "task_scope",
    "warden_read_capabilities",
    "worker_read_capabilities",
]
