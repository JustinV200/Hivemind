"""Define the Honey Store, the Hive's cold-tier knowledge base.

Raw Nectar (unprocessed captured information) is taken in, ripened through a pipeline into Honey
(retrievable, labelled knowledge), and retrieved by Workers before they act. This face re-exports
the models, clearance and scope rules, schema and SQLite store (roadmap 7.2/7.3), Nectar intake
(`nectar/`, 7.4), the ripening pipeline (`ripening/`, 7.5), retrieval (`honey/`, 7.7) and
judge-reviewed label lowering (`lowering/`, ADR-0034), so a caller writes `from
hivemind.honey_store import NectarIntake` without knowing the split (codingrules 5.2). Each
sub-package's own face also exports its finer-grained names.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy). Called by the Queen (intake of Waggle
    deposits, the pre-check, answering queries), the House Bee (ripening, Bee Bread and Cell Wax
    deposits, filing and reviewing label lowerings) and `hive honey` (browsing, maintenance and the
    human's review of lowerings). May
    import `hivemind.cell`, `hivemind.guard`, `hivemind.llm`, `hivemind.manifest`,
    `hivemind.pheromone`, `hivemind.forage` and `hivemind.common`; never `hivemind.memory`,
    `hivemind.supervision` or `hivemind.brood_chamber` (independent Layer-2 siblings,
    `lint-imports` enforces it).

Key invariants:
    - Whatever is not re-exported here is private to this package (codingrules 5.4).
    - Every write reaches the store through intake, the Ripener, an explicit relabel or a
      judge- or human-decided lowering proposal, and every one of them records its `honey.*`
      event in the same transaction (ADR-0031, ADR-0034).

See Also:
    - .claude/codingrules.md section 4 for the layer 2 row this package occupies.
    - docs/adr/0031-honey-store-sqlite-fts5-sqlite-vec.md, docs/adr/0032-embedding-provider-
      and-reembedding-policy.md and docs/adr/0034-honey-label-lowering-is-a-judge-reviewed-
      proposal.md for the decisions this package implements.
    - .claude/roadmap.md phase 7 for the work that populates this package end to end.

Public API:
    - HoneyStoreError, NectarNotFoundError, HoneyNotFoundError, NectarNotRipenableError,
      NectarRejectedError and its ten concrete reasons, LabelLoweringError, InvalidScopeError,
      LoweringNotFoundError, LoweringTransitionError, LoweringIneligibleError,
      LoweringPostconditionError, LoweringInputError, ClearanceJudgeAnswerError (errors): this
      package's error tree.
    - HIVE_SCOPE, NectarProvenance, cell_scope, task_scope, bee_scope, folder_for_scope,
      scope_for_folder, honey_ref, parse_honey_ref, scope_for_nectar, queen_read_capabilities,
      warden_read_capabilities, worker_read_capabilities, readable_globs, is_readable (scope):
      scope strings, folders and the default `honey:read` capability sets.
    - LabelApprover, intake_floor, intake_label, raise_label, reader_ceiling, check_lowering
      (clearance): the pure label rules.
    - Nectar, NectarDraft, NectarOrigin, NectarSource, NectarState, RipenerReading, Honey,
      HoneyDraft, HoneyPart, ReadFilter, TextCandidate, VectorCandidate, HoneyStats (models): the
      value models the store persists.
    - apply_honey_store_migrations, MIGRATIONS_PACKAGE, SUBSYSTEM (schema): this subsystem's
      numbered migration series.
    - HoneyStore, NectarAdded, NectarEvents, HoneyProposal, PruneResult, PruneEvents,
      LoweringEvents, build_match, MAX_MATCH_TOKENS, SqliteHoneyStore (store): the persistence
      protocol, its SQL builder and its durable implementation.
    - HoneyIdentity, honey_event (identity): the one place a HoneyEvent is minted.
    - HoneyAccess (access): one Hive's store, intake, retriever and Ripener, bundled.
    - HoneyBrowser, BrowserDeps, LiveWaxSource, BeeBreadSource, HoneyRelabeller, BrowseError
      (browse): the read-only folder tree over the store, and the operator's relabel; the
      sub-package's own face exports the rest.
    - NectarIntake, NectarSubmission, DepositSource, IntakeResult, handoff_source_key (nectar):
      the one door every deposit comes in through.
    - Ripener, RipenerDeps, PassOutcome, RipenOutcome, PruneOutcome, prune_vectors (ripening):
      Nectar into Honey, and a superseded model's vectors dropped on request (ADR-0033).
    - HoneyRetriever, RetrieverDeps, HoneyReader, HoneySearch, SearchOutcome (honey): hybrid
      retrieval under a reader's scope, clearance and budget.
    - LabelLowering, LoweringDeps, ReviewOutcome, LoweringProposal, LoweringId, LoweringState,
      LoweringFiling, LoweringDecision, ClearanceJudge, ModelClearanceJudge, FakeClearanceJudge,
      ClearanceJudgeRequest, ClearanceVerdict, ClearanceOutcome, lowering_target (lowering):
      judge-reviewed label lowering (ADR-0034); the sub-package's own face exports the rest.
"""

from hivemind.honey_store.access import HoneyAccess
from hivemind.honey_store.browse import (
    BeeBreadSource,
    BrowseError,
    BrowserDeps,
    HoneyBrowser,
    HoneyRelabeller,
    LiveWaxSource,
)
from hivemind.honey_store.clearance import (
    LabelApprover,
    check_lowering,
    intake_floor,
    intake_label,
    raise_label,
    reader_ceiling,
)
from hivemind.honey_store.errors import (
    CellMismatchError,
    ChunkMismatchError,
    ClearanceJudgeAnswerError,
    DepositLengthMismatchError,
    DepositTimedOutError,
    FirstChunkNotAtZeroError,
    HoneyNotFoundError,
    HoneyStoreError,
    InvalidScopeError,
    LabelLoweringError,
    LoweringIneligibleError,
    LoweringInputError,
    LoweringNotFoundError,
    LoweringPostconditionError,
    LoweringTransitionError,
    NectarNotFoundError,
    NectarNotRipenableError,
    NectarRejectedError,
    NectarTooLargeError,
    NightVeilRefusedError,
    OffsetMismatchError,
    Sha256MismatchError,
    TooManyOpenDepositsError,
)
from hivemind.honey_store.honey import (
    HoneyReader,
    HoneyRetriever,
    HoneySearch,
    RetrieverDeps,
    SearchOutcome,
)
from hivemind.honey_store.identity import HoneyIdentity, honey_event
from hivemind.honey_store.lowering import (
    ClearanceJudge,
    ClearanceJudgeRequest,
    ClearanceOutcome,
    ClearanceVerdict,
    FakeClearanceJudge,
    LabelLowering,
    LoweringDecision,
    LoweringDeps,
    LoweringFiling,
    LoweringId,
    LoweringProposal,
    LoweringState,
    ModelClearanceJudge,
    ReviewOutcome,
    lowering_target,
)
from hivemind.honey_store.models import (
    Honey,
    HoneyDraft,
    HoneyPart,
    HoneyStats,
    Nectar,
    NectarDraft,
    NectarOrigin,
    NectarSource,
    NectarState,
    ReadFilter,
    RipenerReading,
    TextCandidate,
    VectorCandidate,
)
from hivemind.honey_store.nectar import (
    DepositSource,
    IntakeResult,
    NectarIntake,
    NectarSubmission,
    handoff_source_key,
)
from hivemind.honey_store.ripening import (
    PassOutcome,
    PruneOutcome,
    Ripener,
    RipenerDeps,
    RipenOutcome,
    prune_vectors,
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
    LoweringEvents,
    NectarAdded,
    NectarEvents,
    PruneEvents,
    PruneResult,
    SqliteHoneyStore,
    build_match,
)

__all__ = [
    "HIVE_SCOPE",
    "MAX_MATCH_TOKENS",
    "MIGRATIONS_PACKAGE",
    "SUBSYSTEM",
    "BeeBreadSource",
    "BrowseError",
    "BrowserDeps",
    "CellMismatchError",
    "ChunkMismatchError",
    "ClearanceJudge",
    "ClearanceJudgeAnswerError",
    "ClearanceJudgeRequest",
    "ClearanceOutcome",
    "ClearanceVerdict",
    "DepositLengthMismatchError",
    "DepositSource",
    "DepositTimedOutError",
    "FakeClearanceJudge",
    "FirstChunkNotAtZeroError",
    "Honey",
    "HoneyAccess",
    "HoneyBrowser",
    "HoneyDraft",
    "HoneyIdentity",
    "HoneyNotFoundError",
    "HoneyPart",
    "HoneyProposal",
    "HoneyReader",
    "HoneyRelabeller",
    "HoneyRetriever",
    "HoneySearch",
    "HoneyStats",
    "HoneyStore",
    "HoneyStoreError",
    "IntakeResult",
    "InvalidScopeError",
    "LabelApprover",
    "LabelLowering",
    "LabelLoweringError",
    "LiveWaxSource",
    "LoweringDecision",
    "LoweringDeps",
    "LoweringEvents",
    "LoweringFiling",
    "LoweringId",
    "LoweringIneligibleError",
    "LoweringInputError",
    "LoweringNotFoundError",
    "LoweringPostconditionError",
    "LoweringProposal",
    "LoweringState",
    "LoweringTransitionError",
    "ModelClearanceJudge",
    "Nectar",
    "NectarAdded",
    "NectarDraft",
    "NectarEvents",
    "NectarIntake",
    "NectarNotFoundError",
    "NectarNotRipenableError",
    "NectarOrigin",
    "NectarProvenance",
    "NectarRejectedError",
    "NectarSource",
    "NectarState",
    "NectarSubmission",
    "NectarTooLargeError",
    "NightVeilRefusedError",
    "OffsetMismatchError",
    "PassOutcome",
    "PruneEvents",
    "PruneOutcome",
    "PruneResult",
    "ReadFilter",
    "RetrieverDeps",
    "ReviewOutcome",
    "RipenOutcome",
    "Ripener",
    "RipenerDeps",
    "RipenerReading",
    "SearchOutcome",
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
    "handoff_source_key",
    "honey_event",
    "honey_ref",
    "intake_floor",
    "intake_label",
    "is_readable",
    "lowering_target",
    "parse_honey_ref",
    "prune_vectors",
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
