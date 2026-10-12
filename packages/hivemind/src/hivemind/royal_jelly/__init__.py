"""Provide the Royal Jelly Lab and Comb Registry: how new tools are described, proven and kept.

The Royal Jelly Lab is the Hive's tool-authoring pipeline: a bee asks for a tool that does not
exist, the lab scaffolds it, the Quarantine Comb (the sandbox every new tool must pass) proves it,
and the Comb Registry (the catalogue of promoted tools) records it at hive or Cell scope. This
package face exports what exists after phase 9's first wave: the tool spec, its validation, the
deterministic package format, the Quarantine Comb's report, the tool state machine, the error
tree and the registry. The Comb, the scaffolder and the promotion flow join later and are not
re-exported yet.

Fits into the Hive:
    Layer 3 (sources of Cells, and capabilities handed down). Called by a Worker or Warden that
    needs a tool that does not exist yet, by the Queen's tick code and by `hive tools`. Calls into
    hivemind.guard (capabilities), hivemind.common and waggle.

Key invariants:
    - A tool is a package of files named by its SHA-256; the registry stores the hash, never the
      bytes, and one hash names one immutable version everywhere.
    - A tool never reaches the registry without a passing Quarantine Comb report; there is no
      force flag.

See Also:
    - docs/adr/0046 (tool spec, Comb Registry and scopes) for the decisions.
    - .claude/codingrules.md section 15 ("Tools never skip the Quarantine Comb").
    - .claude/roadmap.md phase 9 for the steps still to land (Comb, scaffolder, promotion).

Public API:
    - ToolSpec, spec_sha256, validate_spec, SpecValidation (spec): the request and its checks.
    - ToolPackage, pack, unpack, sha256_of, required_files_present (spec): the package format.
    - QuarantineReport, GateResult, ResourceUsage, QuarantineLimits (spec): the Comb's verdict.
    - ToolState, TRANSITIONS, can_transition, transition (tool_state): the tool state machine.
    - CombRegistry, SqliteCombRegistry, InMemoryCombRegistry, CombToolRow, ToolKey,
      PromotionOffer, RegistryChange, QueenPrincipal, WardenPrincipal (registry): the catalogue.
    - RoyalJellyAccess (access): the composition root's bundle of registry, Basket and policy.
    - QuarantineComb, Sandbox, SandboxCell, SandboxSubprocess, SandboxRefusedError, decide_path,
      default_gates, GateContext, BANNED_MODULES, BANNED_NAMES (quarantine_comb): the proof a tool
      needs before promotion.
    - RoyalJellyError and its subclasses (errors): every refusal, each with a stable code.
"""

from hivemind.royal_jelly.access import RoyalJellyAccess
from hivemind.royal_jelly.errors import (
    NeedsReviewError,
    PackageError,
    PromotionRefusedError,
    ReportFailedError,
    ReportMismatchError,
    RollbackRefusedError,
    RoyalJellyError,
    SpecRejectedError,
    ToolNotFoundError,
    ToolTransitionError,
    VersionExistsError,
    WrongCellError,
    WrongPrincipalError,
)
from hivemind.royal_jelly.honey_link import (
    PromotionSite,
    promotion_nectar_text,
    promotion_submission,
)
from hivemind.royal_jelly.promotion import (
    Announcer,
    HoneyDeposit,
    PathDecider,
    PromotionFlow,
    PromotionOutcome,
    PromotionPorts,
    SandboxRunner,
    ToolIdentity,
)
from hivemind.royal_jelly.quarantine_comb import (
    BANNED_MODULES,
    BANNED_NAMES,
    GateContext,
    QuarantineComb,
    Sandbox,
    SandboxCell,
    SandboxRefusedError,
    SandboxSubprocess,
    decide_path,
    default_gates,
)
from hivemind.royal_jelly.registry import (
    ChangeKind,
    CombRegistry,
    CombToolRow,
    InMemoryCombRegistry,
    Principal,
    PromotionOffer,
    QueenPrincipal,
    RegistryChange,
    SqliteCombRegistry,
    ToolKey,
    WardenPrincipal,
    apply_comb_registry_migrations,
)
from hivemind.royal_jelly.scaffold import (
    FakeScaffolder,
    ModelScaffolder,
    Scaffolder,
    ScaffoldFailedError,
    ScaffoldResult,
    ToolRequestBrief,
)
from hivemind.royal_jelly.spec import (
    GATE_ORDER,
    MAX_PACKAGE_BYTES,
    SPEC_FILE,
    TESTS_MODULE,
    TOOL_MODULE,
    GateResult,
    QuarantineLimits,
    QuarantineReport,
    ResourceUsage,
    SpecValidation,
    ToolPackage,
    ToolSpec,
    pack,
    required_files_present,
    sha256_of,
    spec_sha256,
    unpack,
    validate_spec,
)
from hivemind.royal_jelly.tool_state import TRANSITIONS, ToolState, can_transition, transition

__all__ = [
    "BANNED_MODULES",
    "BANNED_NAMES",
    "GATE_ORDER",
    "MAX_PACKAGE_BYTES",
    "SPEC_FILE",
    "TESTS_MODULE",
    "TOOL_MODULE",
    "TRANSITIONS",
    "Announcer",
    "ChangeKind",
    "CombRegistry",
    "CombToolRow",
    "FakeScaffolder",
    "GateContext",
    "GateResult",
    "HoneyDeposit",
    "InMemoryCombRegistry",
    "ModelScaffolder",
    "NeedsReviewError",
    "PackageError",
    "PathDecider",
    "Principal",
    "PromotionFlow",
    "PromotionOffer",
    "PromotionOutcome",
    "PromotionPorts",
    "PromotionRefusedError",
    "PromotionSite",
    "QuarantineComb",
    "QuarantineLimits",
    "QuarantineReport",
    "QueenPrincipal",
    "RegistryChange",
    "ReportFailedError",
    "ReportMismatchError",
    "ResourceUsage",
    "RollbackRefusedError",
    "RoyalJellyAccess",
    "RoyalJellyError",
    "Sandbox",
    "SandboxCell",
    "SandboxRefusedError",
    "SandboxRunner",
    "SandboxSubprocess",
    "ScaffoldFailedError",
    "ScaffoldResult",
    "Scaffolder",
    "SpecRejectedError",
    "SpecValidation",
    "SqliteCombRegistry",
    "ToolIdentity",
    "ToolKey",
    "ToolNotFoundError",
    "ToolPackage",
    "ToolRequestBrief",
    "ToolSpec",
    "ToolState",
    "ToolTransitionError",
    "VersionExistsError",
    "WardenPrincipal",
    "WrongCellError",
    "WrongPrincipalError",
    "apply_comb_registry_migrations",
    "can_transition",
    "decide_path",
    "default_gates",
    "pack",
    "promotion_nectar_text",
    "promotion_submission",
    "required_files_present",
    "sha256_of",
    "spec_sha256",
    "transition",
    "unpack",
    "validate_spec",
]
