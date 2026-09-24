"""Render a recorded GUI action as the evidence a judge reviews: structure as text, screens as PNGs.

An irreversible GUI action is judged after it is applied and before the bee's next step (ADR-0032).
The judge sees exactly what the flight recorder kept, already scrubbed: `judge_evidence` renders a
`RecordedAction` into a `JudgeEvidence`, its steps, URLs, postconditions and bounded snapshot
excerpts as text any judge can read, and its before and after frames as PNG bytes that only a judge
whose model declares vision is shown.

Fits into the Hive:
    Layer 3 (sources of Cells, and capabilities handed down), inside
    `hivemind.exoskeleton.surface`. Called by `surface.core.ExoskeletonSurface.evidence`. Calls
    into `hivemind.exoskeleton.recorder` (RecordedAction) and `hivemind.supervision.capping`
    (JudgeEvidence, its bounds) only.

Key invariants:
    - The text never exceeds MAX_EVIDENCE_CHARS and holds nothing the recorder did not keep.

See Also:
    - hivemind.wardens.spawn.audited_gate for where the judge is asked.
"""

from __future__ import annotations

from hivemind.exoskeleton.recorder import Evidence, RecordedAction
from hivemind.supervision.capping import MAX_EVIDENCE_CHARS, JudgeEvidence

SNAPSHOT_EXCERPT_CHARS = 4_000  # Of each side's accessibility snapshot: enough to see the page.
# A postcondition the gate never reached (an earlier one failed, or it was never applied) is None.
_HELD = {True: "held", False: "not held", None: "not checked"}

__all__ = ["SNAPSHOT_EXCERPT_CHARS", "judge_evidence"]


def judge_evidence(action: RecordedAction) -> JudgeEvidence:
    """Render `action` for a judge: text for structure, the two frames for a vision model.

    Args:
        action: What the recorder kept for one proposal.

    Returns:
        The evidence, text bounded to MAX_EVIDENCE_CHARS.
    """
    sides = (("before", action.before), ("after", action.after))
    shown = [
        (name, side.frame) for name, side in sides if side is not None and side.frame is not None
    ]
    # Near the top so no cut loses it: a vision judge is told which screen is which by this line.
    screens = ", ".join(name for name, _ in shown) or "none captured"
    lines = [f"Tier: {action.tier}; outcome: {action.state}", f"Screens: {screens}", "Steps:"]
    lines += [f"- {step}" for step in action.steps]
    lines += _side("Before", action.before)
    lines += _side("After", action.after) if action.after is not None else ["After: not applied"]
    lines.append("Postconditions:")
    lines += [
        f"- {pc.kind} on {pc.subject!r} expecting {pc.expected!r}: "
        f"{_HELD[pc.has_held]} ({pc.observed})"
        for pc in action.postconditions
    ]
    text = "\n".join(lines)[:MAX_EVIDENCE_CHARS]
    return JudgeEvidence(text=text, frames=tuple(frame.png for _, frame in shown))


def _side(label: str, evidence: Evidence) -> list[str]:
    """Render one side's URL and snapshot excerpt; the frame travels separately."""
    lines = [f"{label}: url={evidence.url or 'none'}"]
    if evidence.snapshot:
        lines.append(f"{label} page (excerpt):\n{evidence.snapshot[:SNAPSHOT_EXCERPT_CHARS]}")
    return lines
