"""Re-export the Hive's security sections: Comb Shield tiers, Honey clearance and the Guard policy.

The ``[security]``/``[honey.clearance]`` sections (``tiers``, phase 3's schema, enforced from
phases 5, 7 and 10) and the ``[guard]`` section (``guard``, the operator's overrides of the Guard
policy, roadmap step 10.2) are one concern, the Hive's security posture, and two files; grouped
here once phases 6, 7 and 10 together took ``hivemind.manifest.schema`` past codingrules 5.6's ten
modules. A caller imports either section's models from here, or from ``hivemind.manifest.schema``
itself, without knowing which module defines them.

Fits into the Hive:
    Layer 1 (foundational services; capacity as data), inside ``hivemind.manifest.schema``.
    Imported by ``hivemind.manifest.schema``'s own face and by ``hivemind.manifest.schema.
    manifest``. Calls into this package's ``tiers`` and ``guard`` only.

Key invariants:
    - This file holds re-exports and __all__ only; no model or validator is defined here
      (codingrules section 5.4).

See Also:
    - .claude/codingrules.md section 5.6 for the ten-module limit this sub-package keeps.
    - hivemind.manifest.schema for the whole schema's own face.

Public API:
    - Security and clearance (tiers): SecuritySection, TierProfile, HoneySection,
      HoneyClearanceSection, ClearanceMatrix, DEFAULT_COMB_SHIELD.
    - Guard policy overrides (guard): GuardSection, GuardRoleSection, GuardBeeSection,
      GuardBeeRuleOverride, UntrustedContentSection, ScanThresholds, their name aliases and
      every default and bound the section validates against.
"""

from hivemind.manifest.schema.security.guard import (
    DEFAULT_AUDIT_RAISE_HOLD_S,
    DEFAULT_AUDIT_RAISE_STEP,
    DEFAULT_COALESCE_WINDOW_S,
    DEFAULT_DIRE_PATTERNS,
    DEFAULT_GUARD_BEE_INTERVAL_S,
    DEFAULT_JUDGE_TIMEOUT_S,
    DEFAULT_MAX_SCAN_CHARS,
    DEFAULT_REQUEST_CONFIDENCE,
    DEFAULT_REQUESTS_PER_HOUR,
    MAX_GUARD_WINDOW_S,
    MAX_REQUESTS_PER_HOUR,
    GuardActionName,
    GuardBeeRuleOverride,
    GuardBeeSection,
    GuardConfidenceName,
    GuardRoleSection,
    GuardSection,
    ScanThresholds,
    UntrustedContentSection,
)
from hivemind.manifest.schema.security.tiers import (
    DEFAULT_COMB_SHIELD,
    ClearanceMatrix,
    HoneyClearanceSection,
    HoneySection,
    SecuritySection,
    TierProfile,
)

__all__ = [
    "DEFAULT_AUDIT_RAISE_HOLD_S",
    "DEFAULT_AUDIT_RAISE_STEP",
    "DEFAULT_COALESCE_WINDOW_S",
    "DEFAULT_COMB_SHIELD",
    "DEFAULT_DIRE_PATTERNS",
    "DEFAULT_GUARD_BEE_INTERVAL_S",
    "DEFAULT_JUDGE_TIMEOUT_S",
    "DEFAULT_MAX_SCAN_CHARS",
    "DEFAULT_REQUESTS_PER_HOUR",
    "DEFAULT_REQUEST_CONFIDENCE",
    "MAX_GUARD_WINDOW_S",
    "MAX_REQUESTS_PER_HOUR",
    "ClearanceMatrix",
    "GuardActionName",
    "GuardBeeRuleOverride",
    "GuardBeeSection",
    "GuardConfidenceName",
    "GuardRoleSection",
    "GuardSection",
    "HoneyClearanceSection",
    "HoneySection",
    "ScanThresholds",
    "SecuritySection",
    "TierProfile",
    "UntrustedContentSection",
]
