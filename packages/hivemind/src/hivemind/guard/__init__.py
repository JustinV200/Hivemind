"""The Guard: the Hive's capability model, access-level data and policy engine (ADR-0039).

Every action in the Hive is authorised against an explicit capability model. A capability is one
thing a principal (the operator, the Queen, a Warden, a Worker, a Swarm device or an enrolled
client device) may do, written `family` or `family:scope`; a `CapabilitySet` is everything one
principal holds, and sets only narrow down the tree (`capabilities`). `access` says, as data,
which Cell-effect families each `AccessLevel` (a Real Cell's READ_ONLY/SCRATCH/FULL tier) permits
and narrows a set to it. `policy` is the engine proper: a `GuardPolicy` (each role's default set,
the hive-wide deny list and the escalation table, from the shipped `defaults/policy.toml` with
the manifest's `[guard]` on top), the `EnforcementPoint` enum, and the pure `evaluate` that
decides one request with a rule and a reason. `enforcer` is the one effectful module: the adapter
every enforcement point calls, which records each refusal as a `guard.denied` trail event before
returning it. The security tier enums themselves (`AccessLevel`, `CombShieldLevel`,
`HoneyClearance`) live in `hivemind.cell.tiers`; guard interprets them. `file_urls` (phase 6,
ADR-0034) is the one lexical rule for whether a browser's `file://` URL stays inside the
directories its lease lets it read.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy). Called by every layer above it, before
    an action is allowed to proceed. Calls into `hivemind.cell` (the tier enums, RequestOrigin,
    CellIdentity), `hivemind.forage` (ModelSlot), `hivemind.manifest` (the `[guard]` shapes),
    `hivemind.pheromone` (the `guard.denied` event, from `enforcer` only), `hivemind.common` and
    waggle.

Key invariants:
    - Nothing here widens a set: `CapabilitySet.attenuate`, `access.cap_to_access` and
      `policy.warden_set` only ever return subsets, and `CapabilitySet` has no union
      (codingrules section 15).
    - Only holding the needed capability ever turns a request into an allow.
    - `capabilities`, `access` and `policy` are pure (the policy loader reads its file once, at
      start); `enforcer` is the one module that writes, and only to the Pheromone Trail.

See Also:
    - docs/adr/0039-capability-model-attenuation-and-enforcement-points.md for the decision.
    - .claude/codingrules.md section 4 for the layer 2 row this package occupies, and section 15
      for the least-privilege rules it encodes.
    - .claude/roadmap.md phase 10 steps 10.1, 10.2 and 10.7 for this package's scope, and 10.3
      for the enforcement points that call it.

Public API:
    - CapabilityFamily, ScopeKind, Capability, CapabilitySet, glob_literal, EXOSKELETON_SCOPES:
      the capability grammar, the escape every path embedded in a glob scope goes through, and
      the four peripherals an `exoskeleton` scope may name (capabilities).
    - CELL_EFFECT_FAMILIES, governs, admits, ceiling_for, cap_to_access, fill_scratch,
      SCRATCH_PLACEHOLDER: what each AccessLevel permits, as data (access).
    - EnforcementPoint, PrincipalKind, PrincipalRef, PolicyContext, PolicyRequest,
      EscalationAction, PolicyDecision, GuardPolicy, load_guard_policy, evaluate, role_set,
      warden_set, proposed_set, worker_role_name: the policy engine (policy).
    - floor_decision, TIER_FLOOR_RULE, STATE_FLOOR_RULE, HiveState, comparable_path, ControlLink,
      GoalRequestFacts: the floors every decision runs first, the Hive's own state they protect
      and the facts they read (policy, roadmap steps 10.3a-d).
    - queen_principal, warden_principal, worker_principal, QUEEN_ROLE, WARDEN_ROLE: who each bee
      acts as at a point, and the two roots' role names (policy).
    - AUTHORISED_AT, NOT_ACTIONS, PENDING_POINTS, classify: every trail kind's classification
      and the points still pending (policy, roadmap step 10.3).
    - Enforcer: the effectful adapter every enforcement point calls: `check` and, for a refusal
      the point decides itself, `refuse` (enforcer).
    - WatchObservation, WATCH_OBSERVATIONS, watch_permits: what watch mode may observe on a Real
      Cell, bounded by READ_ONLY and never the screen or the input (watch, roadmap step 10.7).
    - GuardReport, GuardAction, GuardConfidence, GuardRequestDoor, REQUEST_ACTIONS,
      new_guard_report_id, GuardReportId: what the Guard Bee found and asks the Queen to do,
      and the one door a request reaches her by (report, roadmap steps 10.6 and 10.6a).
    - ContentScanner, ScanSite, ScanSource, ScanAction, ScanVerdict, ScanRecorder,
      ContentHasher, default_content_scanner, load_scan_patterns, score_text, decide,
      thresholds_for, INJECTION_SUSPECTED_KIND, SCANNER_KEY_NAME, HASH_PREFIX: the deterministic
      untrusted-content scanner every outside text passes before a model reads it (scanner,
      roadmap step 10.6b); its full API is on `hivemind.guard.scanner`.
    - file_url_escapes, file_url_path, is_file_url, is_within, FILE_SCHEME, LOCAL_FILE_HOSTS:
      whether a file URL stays inside the directories a lease lets its browser read (file_urls).
    - GuardError, InvalidCapabilityError, CapabilityWideningError, GuardPolicyError,
      UnresolvableHostError: this package's error tree (errors).
"""

from hivemind.guard.access import (
    CELL_EFFECT_FAMILIES,
    SCRATCH_PLACEHOLDER,
    admits,
    cap_to_access,
    ceiling_for,
    fill_scratch,
    governs,
)
from hivemind.guard.capabilities import (
    EXOSKELETON_SCOPES,
    Capability,
    CapabilityFamily,
    CapabilitySet,
    ScopeKind,
    glob_literal,
)
from hivemind.guard.enforcer import Enforcer
from hivemind.guard.errors import (
    CapabilityWideningError,
    GuardError,
    GuardPolicyError,
    InvalidCapabilityError,
    UnresolvableHostError,
)
from hivemind.guard.file_urls import (
    FILE_SCHEME,
    LOCAL_FILE_HOSTS,
    file_url_escapes,
    file_url_path,
    is_file_url,
    is_within,
)
from hivemind.guard.policy import (
    AUTHORISED_AT,
    NOT_ACTIONS,
    PENDING_POINTS,
    QUEEN_ROLE,
    STATE_FLOOR_RULE,
    TIER_FLOOR_RULE,
    WARDEN_ROLE,
    ControlLink,
    EnforcementPoint,
    EscalationAction,
    GoalRequestFacts,
    GuardPolicy,
    HiveState,
    PolicyContext,
    PolicyDecision,
    PolicyRequest,
    PrincipalKind,
    PrincipalRef,
    classify,
    comparable_path,
    evaluate,
    floor_decision,
    load_guard_policy,
    proposed_set,
    queen_principal,
    role_set,
    warden_principal,
    warden_set,
    worker_principal,
    worker_role_name,
)
from hivemind.guard.report import (
    REQUEST_ACTIONS,
    GuardAction,
    GuardConfidence,
    GuardReport,
    GuardReportId,
    GuardRequestDoor,
    new_guard_report_id,
)
from hivemind.guard.scanner import (
    HASH_PREFIX,
    INJECTION_SUSPECTED_KIND,
    SCANNER_KEY_NAME,
    ContentHasher,
    ContentScanner,
    ScanAction,
    ScanRecorder,
    ScanSite,
    ScanSource,
    ScanVerdict,
    decide,
    default_content_scanner,
    load_scan_patterns,
    score_text,
    thresholds_for,
)
from hivemind.guard.watch import WATCH_OBSERVATIONS, WatchObservation, watch_permits

__all__ = [
    "AUTHORISED_AT",
    "CELL_EFFECT_FAMILIES",
    "EXOSKELETON_SCOPES",
    "FILE_SCHEME",
    "HASH_PREFIX",
    "INJECTION_SUSPECTED_KIND",
    "LOCAL_FILE_HOSTS",
    "NOT_ACTIONS",
    "PENDING_POINTS",
    "QUEEN_ROLE",
    "REQUEST_ACTIONS",
    "SCANNER_KEY_NAME",
    "SCRATCH_PLACEHOLDER",
    "STATE_FLOOR_RULE",
    "TIER_FLOOR_RULE",
    "WARDEN_ROLE",
    "WATCH_OBSERVATIONS",
    "Capability",
    "CapabilityFamily",
    "CapabilitySet",
    "CapabilityWideningError",
    "ContentHasher",
    "ContentScanner",
    "ControlLink",
    "EnforcementPoint",
    "Enforcer",
    "EscalationAction",
    "GoalRequestFacts",
    "GuardAction",
    "GuardConfidence",
    "GuardError",
    "GuardPolicy",
    "GuardPolicyError",
    "GuardReport",
    "GuardReportId",
    "GuardRequestDoor",
    "HiveState",
    "InvalidCapabilityError",
    "PolicyContext",
    "PolicyDecision",
    "PolicyRequest",
    "PrincipalKind",
    "PrincipalRef",
    "ScanAction",
    "ScanRecorder",
    "ScanSite",
    "ScanSource",
    "ScanVerdict",
    "ScopeKind",
    "UnresolvableHostError",
    "WatchObservation",
    "admits",
    "cap_to_access",
    "ceiling_for",
    "classify",
    "comparable_path",
    "decide",
    "default_content_scanner",
    "evaluate",
    "file_url_escapes",
    "file_url_path",
    "fill_scratch",
    "floor_decision",
    "glob_literal",
    "governs",
    "is_file_url",
    "is_within",
    "load_guard_policy",
    "load_scan_patterns",
    "new_guard_report_id",
    "proposed_set",
    "queen_principal",
    "role_set",
    "score_text",
    "thresholds_for",
    "warden_principal",
    "warden_set",
    "watch_permits",
    "worker_principal",
    "worker_role_name",
]
