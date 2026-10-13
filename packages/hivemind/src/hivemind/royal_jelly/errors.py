"""Define the Royal Jelly Lab's error tree: rejected specs, bad packages, refused promotions.

The Royal Jelly Lab is the Hive's tool-authoring pipeline: a bee asks for a tool, the lab
scaffolds it, the Quarantine Comb (the sandbox every new tool must pass) proves it, and the Comb
Registry (the catalogue of promoted tools) records it. Every subsystem roots its errors in
`hivemind.common.errors.HiveMindError` (codingrules section 10); this module is that root for
`hivemind.royal_jelly`. `PromotionRefusedError` is a family of six concrete subclasses rather than
one class with a per-instance code, because `HiveMindError.code` is a `ClassVar[str]` and mypy
--strict refuses to narrow a `ClassVar` into an instance attribute (the same reason
`hivemind.honey_store.errors.NectarRejectedError` is a family): `except PromotionRefusedError:`
catches every reason at once, and each subclass's `code` is the stable string a caller matches on.

Fits into the Hive:
    Layer 3 (sources of Cells, and capabilities handed down). Raised by `hivemind.royal_jelly.
    spec` (package and spec refusals), `hivemind.royal_jelly.tool_state` (a forbidden state edge)
    and `hivemind.royal_jelly.registry` (every refusal `CombRegistry` makes). Calls into
    `hivemind.common.errors` only.

Key invariants:
    - Every concrete class sets its own `code`; no two unrelated classes share one.
    - Every `PromotionRefusedError` subclass also sets `refusal`, the short reason string (one of
      `report_failed`, `report_mismatch`, `wrong_principal`, `wrong_cell`, `version_exists`,
      `needs_review`) that the no-bypass test and the trail event carry.

See Also:
    - docs/adr/0046 (tool spec, Comb Registry and scopes) for the refusals these classes carry.
    - .claude/codingrules.md section 10 for the errors rules this module follows.
    - hivemind.royal_jelly.registry.rules for the pure checks that raise the promotion refusals.
"""

from __future__ import annotations

from typing import ClassVar

from hivemind.common.errors import HiveMindError, NotFoundError

__all__ = [
    "NeedsReviewError",
    "PackageError",
    "PromotionRefusedError",
    "ReportFailedError",
    "ReportMismatchError",
    "RollbackRefusedError",
    "RoyalJellyError",
    "SpecRejectedError",
    "ToolNotFoundError",
    "ToolTransitionError",
    "VersionExistsError",
    "WrongCellError",
    "WrongPrincipalError",
]


class RoyalJellyError(HiveMindError):
    """Root of every error `hivemind.royal_jelly` raises on purpose.

    Subclass this for a specific failure; code with nothing more specific to say may raise it.
    """

    code: ClassVar[str] = "hivemind.royal_jelly.error"


class SpecRejectedError(RoyalJellyError):
    """Raise when a `ToolSpec` fails `validate_spec`; carries every reason, not only the first."""

    code: ClassVar[str] = "hivemind.royal_jelly.spec_rejected"

    def __init__(self, tool_name: str, reasons: tuple[str, ...]) -> None:
        """Build the error for a spec that `validate_spec` refused.

        Args:
            tool_name: The spec's tool name, so a log line names what was refused.
            reasons: Every refusal reason `validate_spec` returned; never empty.
        """
        super().__init__(f"Tool spec {tool_name!r} was rejected: {'; '.join(reasons)}")
        self.tool_name = tool_name
        self.reasons = reasons


class PackageError(RoyalJellyError):
    """Raise when a tool package cannot be packed or unpacked safely (a zip-slip, a bomb, junk)."""

    code: ClassVar[str] = "hivemind.royal_jelly.package_invalid"


class ToolTransitionError(RoyalJellyError):
    """Raise when a tool is asked to take a state edge `TRANSITIONS` does not list."""

    code: ClassVar[str] = "hivemind.royal_jelly.tool_transition_refused"

    def __init__(self, current: str, target: str) -> None:
        """Build the error for a refused edge.

        Args:
            current: The tool's current state name (`"RETIRED"`).
            target: The state it was asked to move to.
        """
        super().__init__(
            f"A tool cannot move from {current} to {target}: the transition table "
            "(hivemind.royal_jelly.tool_state) has no such edge."
        )
        self.current = current
        self.target = target


class ToolNotFoundError(NotFoundError, RoyalJellyError):
    """Raise when the Comb Registry holds no matching tool, version or current row."""

    code: ClassVar[str] = "hivemind.royal_jelly.tool_not_found"


class RollbackRefusedError(RoyalJellyError):
    """Raise when a rollback target is not a legal one: retired, current, or not older."""

    code: ClassVar[str] = "hivemind.royal_jelly.rollback_refused"


class PromotionRefusedError(RoyalJellyError):
    """Root of the Comb Registry's six promotion refusals; catch this to handle any alike.

    Every concrete subclass fixes `code` and `refusal` (ADR-0046). The registry's `promote` is the
    only writer that creates a PROMOTED row, and these are the only reasons it says no.
    """

    code: ClassVar[str] = "hivemind.royal_jelly.promotion_refused"
    refusal: ClassVar[str] = "refused"


class ReportFailedError(PromotionRefusedError):
    """Raise when the Quarantine Comb's report did not pass: there is no force flag (section 15)."""

    code: ClassVar[str] = "hivemind.royal_jelly.promotion_refused.report_failed"
    refusal: ClassVar[str] = "report_failed"


class ReportMismatchError(PromotionRefusedError):
    """Raise when the report proves a different spec or package than the one offered."""

    code: ClassVar[str] = "hivemind.royal_jelly.promotion_refused.report_mismatch"
    refusal: ClassVar[str] = "report_mismatch"


class WrongPrincipalError(PromotionRefusedError):
    """Raise when the principal may not act at that scope: HIVE needs the Queen, CELL a Warden."""

    code: ClassVar[str] = "hivemind.royal_jelly.promotion_refused.wrong_principal"
    refusal: ClassVar[str] = "wrong_principal"


class WrongCellError(PromotionRefusedError):
    """Raise when a Warden acts on a CELL-scope tool that belongs to a different Cell."""

    code: ClassVar[str] = "hivemind.royal_jelly.promotion_refused.wrong_cell"
    refusal: ClassVar[str] = "wrong_cell"


class VersionExistsError(PromotionRefusedError):
    """Raise when that (name, scope, cell) already holds that version: versions are immutable."""

    code: ClassVar[str] = "hivemind.royal_jelly.promotion_refused.version_exists"
    refusal: ClassVar[str] = "version_exists"


class NeedsReviewError(PromotionRefusedError):
    """Raise when a REAL_CELL-path report at HIVE scope lacks the Queen's awake review."""

    code: ClassVar[str] = "hivemind.royal_jelly.promotion_refused.needs_review"
    refusal: ClassVar[str] = "needs_review"
