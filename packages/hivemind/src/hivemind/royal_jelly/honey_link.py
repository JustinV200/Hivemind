"""Describe a promoted tool as Nectar, so a bee can find it through Honey (roadmap 9.8).

Honey describes, the Comb permits, the Basket stores (ADR-0048): a promotion deposits one Nectar
so retrieval can surface the tool to a bee that needs it, while the Comb Registry stays the source
of truth for whether it may run. This module builds that deposit as pure values.
`promotion_nectar_text` renders a short markdown description (name, version, scope, platforms,
capabilities, what it does, which path proved it, the package hash); `promotion_submission` wraps
it in the `NectarSubmission` the Honey intake takes, so the composition root's `HoneyDeposit`
adapter is one line: `await intake.submit(promotion_submission(...))`.

Fits into the Hive:
    Layer 3 (sources of Cells, and capabilities handed down), inside royal_jelly. Called by the
    composition root's `HoneyDeposit` adapter; calls into `hivemind.honey_store` (the submission
    value) and the spec package. Layer 3 may import Layer 2, so no port is needed here.

Key invariants:
    - The text names a tool, never carries its source: Honey is data and a tool's code stays in
      the Basket under its hash.
    - A HIVE-scope tool is shared knowledge: a FINDING of TASK_OUTCOME origin (hive scope in
      Honey), declared `C0`. A CELL-scope tool stays with its Cell: a TOOL_RESULT of BEE origin
      with no task and no bee (Cell scope in Honey), declared `C1`, so another Cell's bees never
      find it. The intake floor raises either to `C2` when the Cell is borrowed.
    - `source_key` is `tool:<name>:<version>`, so redepositing a version dedupes onto one row.

See Also:
    - docs/adr/0046 (tool spec, Comb Registry and scopes) and docs/adr/0048 (the Basket).
    - hivemind.honey_store.nectar for the intake that takes the submission.
"""

from __future__ import annotations

from dataclasses import dataclass

from hivemind.cell import CombShieldLevel, HoneyClearance
from hivemind.honey_store import NectarOrigin, NectarSubmission
from hivemind.royal_jelly.spec import QuarantineReport, ToolSpec
from waggle.clock import Clock
from waggle.ids import CellId
from waggle.messages.honey import NectarKind
from waggle.messages.honey.exchange import MAX_TITLE_CHARS
from waggle.messages.tool.authoring import ToolScope

TOOL_NECTAR_MEDIA_TYPE = "text/markdown"  # The description is markdown prose.
TOOL_SOURCE_KEY_PREFIX = "tool:"  # Dedupe key family; `tool:<name>:<version>`.
_NO_CAPABILITIES = "none"  # Written when a tool needs nothing beyond its session.

__all__ = [
    "TOOL_NECTAR_MEDIA_TYPE",
    "TOOL_SOURCE_KEY_PREFIX",
    "PromotionSite",
    "promotion_nectar_text",
    "promotion_submission",
]


def promotion_nectar_text(spec: ToolSpec, report: QuarantineReport) -> str:
    """Render the Nectar text that describes a promoted tool.

    Args:
        spec: The promoted spec; its description is the tool's own model-facing text.
        report: The passing report that proved it; supplies the path and the sandbox facts.

    Returns:
        Markdown: a heading, the facts a bee searches by, the description, and the proof. It holds
        the package hash, never the package.
    """
    scope = spec.scope.value if spec.cell_id is None else f"{spec.scope.value} ({spec.cell_id})"
    capabilities = ", ".join(spec.required_capabilities) or _NO_CAPABILITIES
    platforms = ", ".join(os.value for os in spec.supported_os)
    return "\n".join(
        [
            f"# Tool {spec.name} {spec.version}",
            "",
            f"Scope: {scope}",
            f"Platforms: {platforms}",
            f"Capabilities it acts under: {capabilities}",
            "",
            spec.description,
            "",
            f"Proved by the Quarantine Comb on the {report.path.value} path "
            f"({len(report.gates)} gates passed).",
            f"Package: sha256 {report.package_sha256}.",
            "Call it by name once your Warden has handed it to you; this note is a description, "
            "not a grant.",
        ]
    )


@dataclass(frozen=True, slots=True)
class PromotionSite:
    """Where a promotion was recorded: the Cell, its tier and whether it is a borrowed Cell."""

    cell_id: CellId  # The Cell the promotion was recorded on (the Queen's own, at HIVE scope).
    tier: CombShieldLevel  # Its Comb Shield tier, from the depositor's own record of it.
    from_borrowed_cell: bool = False  # True for a Real Cell, so intake raises the label to C2.


def promotion_submission(
    spec: ToolSpec,
    report: QuarantineReport,
    package_sha256: str,
    site: PromotionSite,
    clock: Clock,
) -> NectarSubmission:
    """Build the Nectar submission a promotion deposits.

    Args:
        spec: The promoted spec.
        report: The passing report that proved it.
        package_sha256: The promoted package's hash; the key is the version, the text the hash.
        site: The Cell the promotion was recorded on, its Comb Shield tier and whether it is a
            borrowed Cell, from the depositor's own record of it, never from a message.
        clock: Supplies `observed_at`.

    Returns:
        A submission whose kind, origin and declared label follow the tool's scope (module
        docstring); a tool description holds no operator data of its own, and the intake floor
        decides the rest.
    """
    text = promotion_nectar_text(spec, report)
    # The package hash is part of the proof; a mismatch here means the caller passed the wrong
    # package, which would deposit a description of one tool under another's hash.
    if package_sha256 != report.package_sha256:
        raise ValueError(
            f"promotion_submission got package {package_sha256!r} but the report proved "
            f"{report.package_sha256!r}."
        )
    # Honey scopes a deposit by its origin and kind, and has no "tool" kind: the pair below is the
    # one that lands a HIVE tool in shared knowledge and a CELL tool in its own Cell's history.
    hive_wide = spec.scope is ToolScope.HIVE
    return NectarSubmission(
        kind=NectarKind.FINDING if hive_wide else NectarKind.TOOL_RESULT,
        origin=NectarOrigin.TASK_OUTCOME if hive_wide else NectarOrigin.BEE,
        media_type=TOOL_NECTAR_MEDIA_TYPE,
        title=f"Tool {spec.name} {spec.version}: {spec.description}"[:MAX_TITLE_CHARS],
        content=text.encode("utf-8"),
        task_id=None,
        cell_id=site.cell_id,
        observed_at=clock.now(),
        declared=HoneyClearance.C0 if hive_wide else HoneyClearance.C1,
        from_borrowed_cell=site.from_borrowed_cell,
        tier=site.tier,
        source_key=f"{TOOL_SOURCE_KEY_PREFIX}{spec.name}:{spec.version}",
    )
