"""Play a flight recording back: a self-contained HTML page, and the pixel-free summary beside it.

A flight recording (roadmap step 6.6, ADR-0032) is evidence of every GUI action taken while an
Exoskeleton (a Cell's optional display, input, audio and browser) was attached. Playback shows it
the two ways its readers need. `render_html` builds one page a human opens in any browser: every
action in order with its steps, the before and after frames side by side (inline as `data:` URIs,
so the page has no external asset and survives being mailed or archived), the URL on each side,
the accessibility snapshot folded away, each declared postcondition with whether it held and what
was observed, the terminal state and the rollback method. The page carries no script, and its
Content-Security-Policy forbids any, so recorded web text cannot run even if it slipped past the
escaping. `summarize` builds `RecordingSummary`, the same content with every frame reduced to its
digest and size and every snapshot to its length: the JSON form `hive recordings show --json`
prints and the Observation Hive's playback (roadmap step 12.4) reads. Both show the steps as their
one-line descriptions; the typed steps (`RecordedAction.gui`) stay in the store, for exporting a
verified recording as a browser procedure (roadmap step 6.7).

Fits into the Hive:
    Layer 3 (sources of Cells, and capabilities handed down), inside
    `hivemind.exoskeleton.recorder`. Called by `hive recordings show|export`
    (`hivemind.cli.recordings`) and, later, the Observation Hive. Calls into
    `hivemind.exoskeleton.frames`, `recorder.models`, pydantic, waggle and the standard library.

Key invariants:
    - Pure: no I/O. The page and the summary hold exactly what the RecordedActions hold, which
      the recorder already redacted at the source; nothing here adds or removes a secret.
    - Every recorded string reaches the page through `html.escape`.
    - Frame bytes appear only as `data:image/png;base64` image sources in the page, never in the
      summary.

See Also:
    - docs/adr/0032-gui-actions-are-capped-recorded-and-rolled-back-by-checkpoint.md.
    - hivemind.exoskeleton.recorder.models for what a recording holds.
    - .claude/roadmap.md step 12.4 for the Observation Hive's playback.
"""

from __future__ import annotations

import base64
import html
from collections.abc import Sequence
from typing import Final, Literal

from pydantic import BaseModel, ConfigDict, Field

from hivemind.exoskeleton.frames import PNG_MEDIA_TYPE, Frame
from hivemind.exoskeleton.recorder.models import (
    Evidence,
    RecordedAction,
    RecordedPostcondition,
    RecordingInfo,
)
from waggle.messages.base import UtcDatetime

SUMMARY_VERSION: Final = 1  # RecordingSummary's shape; raised when a reader must tell old from new.
DIGEST_CHARS = 12  # How much of a frame's sha256 the page and the CLI show: enough to tell apart.
# The page may load nothing from anywhere and run nothing: images only from its own data: URIs,
# styles only from its own <style> element.
_CONTENT_POLICY = "default-src 'none'; img-src data:; style-src 'unsafe-inline'"
_STYLE = """
body { font: 14px/1.45 system-ui, sans-serif; margin: 24px auto; max-width: 1400px;
  padding: 0 16px; color: #1d1d1f; background: #ffffff; }
h1 { font-size: 20px; } h2 { font-size: 16px; margin: 0 0 8px; } h3 { font-size: 14px; }
dl { display: grid; grid-template-columns: max-content 1fr; gap: 2px 12px; margin: 0 0 8px; }
dt { color: #5f6368; } dd { margin: 0; overflow-wrap: anywhere; }
section { border: 1px solid #c9ccd1; border-radius: 6px; padding: 12px 16px; margin: 16px 0; }
.sides { display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); gap: 16px; }
figure { margin: 0; } figcaption { font-weight: 600; margin-bottom: 4px; }
img { max-width: 100%; height: auto; border: 1px solid #c9ccd1; }
table { border-collapse: collapse; width: 100%; }
th, td { border: 1px solid #c9ccd1; padding: 4px 6px; text-align: left; overflow-wrap: anywhere; }
pre { white-space: pre-wrap; max-height: 320px; overflow: auto; padding: 8px; background: #f3f4f6; }
.none, .meta { color: #5f6368; } .failed { color: #b3261e; font-weight: 600; }
@media (prefers-color-scheme: dark) {
  body { color: #e8eaed; background: #17181a; } pre { background: #26282b; }
  .none, .meta, dt { color: #9aa0a6; } .failed { color: #f28b82; } }
"""
# What the Held column says for each value of RecordedPostcondition.has_held.
_HELD_TEXT: dict[bool | None, str] = {True: "held", False: "failed", None: "not checked"}

__all__ = [
    "DIGEST_CHARS",
    "SUMMARY_VERSION",
    "ActionSummary",
    "FrameRef",
    "RecordingSummary",
    "SideSummary",
    "render_html",
    "summarize",
]

_FROZEN = ConfigDict(frozen=True, extra="forbid")


class FrameRef(BaseModel):
    """A frame named by its digest and size: what a summary holds instead of the pixels."""

    model_config = _FROZEN

    sha256: str = Field(
        description="Hex sha256 of the PNG; matches the page's image and the store."
    )
    width: int = Field(ge=1, description="Pixel width.")
    height: int = Field(ge=1, description="Pixel height.")
    captured_at: UtcDatetime = Field(description="When the capture completed.")


class SideSummary(BaseModel):
    """One side of an action (before or after), without its pixels or its snapshot's text."""

    model_config = _FROZEN

    frame: FrameRef | None = Field(default=None, description="The screen, by digest; None: none.")
    url: str | None = Field(default=None, description="The page URL, credentials already masked.")
    snapshot_chars: int = Field(
        default=0, ge=0, description="Length of the kept accessibility snapshot; 0 when none."
    )


class ActionSummary(BaseModel):
    """One recorded GUI proposal as playback lists it."""

    model_config = _FROZEN

    number: int = Field(ge=1, description="Its 1-based position in the recording.")
    proposal_id: str = Field(description="The Capping proposal it records.")
    tier: str = Field(description="The RiskTier value it was capped at.")
    state: str = Field(description="The terminal ProposalState value.")
    rollback: str | None = Field(
        description="The RollbackMethod value, or None when not rolled back."
    )
    steps: tuple[str, ...] = Field(description="Each step's one-line description, redacted.")
    before: SideSummary = Field(description="The Cell just before the steps ran.")
    after: SideSummary | None = Field(
        description="The Cell once the gate was done; None: never ran."
    )
    postconditions: tuple[RecordedPostcondition, ...] = Field(
        description="Each declared postcondition with whether it held and what was observed."
    )
    started_at: UtcDatetime = Field(description="When the before-evidence was taken.")
    finished_at: UtcDatetime = Field(description="When the proposal reached its terminal state.")


class RecordingSummary(BaseModel):
    """A whole recording without its pixels: the header and every action, in order."""

    model_config = _FROZEN

    version: Literal[1] = Field(default=SUMMARY_VERSION, description="This summary's shape.")
    recording: RecordingInfo = Field(description="The recording's header.")
    actions: tuple[ActionSummary, ...] = Field(description="Every recorded action, oldest first.")


def summarize(info: RecordingInfo, actions: Sequence[RecordedAction]) -> RecordingSummary:
    """Summarise one recording: its header and every action's evidence, frames as digests.

    Args:
        info: The recording's header.
        actions: Its actions, in the order the store returned them.

    Returns:
        The summary, safe to print or ship as JSON: it holds no image bytes.
    """
    return RecordingSummary(
        recording=info,
        actions=tuple(
            _action_summary(number, action) for number, action in enumerate(actions, start=1)
        ),
    )


def render_html(info: RecordingInfo, actions: Sequence[RecordedAction]) -> str:
    """Render one recording as a self-contained HTML page: no script, no external asset.

    Args:
        info: The recording's header.
        actions: Its actions, in the order the store returned them.

    Returns:
        The page's full text, for a file or an HTTP response; never for a log.
    """
    body = "".join(_action_section(number, action) for number, action in enumerate(actions, 1))
    if not actions:
        body = '<p class="none">No actions were recorded.</p>'
    return (
        '<!DOCTYPE html>\n<html lang="en"><head><meta charset="utf-8">'
        f'<meta http-equiv="Content-Security-Policy" content="{_CONTENT_POLICY}">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>Flight recording {_e(info.recording_id)}</title><style>{_STYLE}</style></head>"
        f"<body>{_page_header(info, len(actions))}<main>{body}</main></body></html>\n"
    )


def _action_summary(number: int, action: RecordedAction) -> ActionSummary:
    """Summarise one action: the same fields, each side reduced by `_side_summary`."""
    return ActionSummary(
        number=number,
        proposal_id=action.proposal_id,
        tier=action.tier,
        state=action.state,
        rollback=action.rollback,
        steps=action.steps,
        before=_side_summary(action.before),
        after=_side_summary(action.after) if action.after is not None else None,
        postconditions=action.postconditions,
        started_at=action.started_at,
        finished_at=action.finished_at,
    )


def _side_summary(evidence: Evidence) -> SideSummary:
    """Reduce one side's evidence: the frame to a FrameRef, the snapshot to its length."""
    frame = evidence.frame
    return SideSummary(
        frame=_frame_ref(frame) if frame is not None else None,
        url=evidence.url,
        snapshot_chars=len(evidence.snapshot) if evidence.snapshot is not None else 0,
    )


def _frame_ref(frame: Frame) -> FrameRef:
    """Name `frame` by digest, size and capture time."""
    return FrameRef(
        sha256=frame.sha256, width=frame.width, height=frame.height, captured_at=frame.captured_at
    )


def _page_header(info: RecordingInfo, count: int) -> str:
    """Render the page header: which Cell, which task, what clearance, when, how many actions."""
    facts = (
        ("Recording", info.recording_id),
        ("Cell", info.cell_id),
        ("Task", info.task_id or "-"),
        ("Clearance", info.clearance),
        ("Started", info.started_at.isoformat()),
        ("Actions", str(count)),
    )
    return f"<header><h1>Flight recording</h1>{_definitions(facts)}</header>"


def _action_section(number: int, action: RecordedAction) -> str:
    """Render one action: its facts, its steps, both sides, and its postconditions."""
    facts = (
        ("Proposal", action.proposal_id),
        ("Tier", action.tier),
        ("Terminal state", action.state),
        ("Rollback", action.rollback or "none"),
        ("Started", action.started_at.isoformat()),
        ("Finished", action.finished_at.isoformat()),
    )
    items = "".join(f"<li>{_e(step)}</li>" for step in action.steps)
    steps = f"<ol>{items}</ol>" if items else '<p class="none">No steps.</p>'
    return (
        f'<section id="action-{number}"><h2>{number}. {_e(action.state)}</h2>'
        f"{_definitions(facts)}<h3>Steps</h3>{steps}"
        f'<div class="sides">{_side("Before", action.before)}{_side("After", action.after)}</div>'
        f"{_postconditions(action.postconditions)}</section>"
    )


def _side(label: str, evidence: Evidence | None) -> str:
    """Render one side: the frame, the URL and the folded snapshot, or why there is nothing."""
    if evidence is None:
        # The recorder keeps no after-evidence for a proposal the gate rejected before it ran.
        return (
            f'<figure><figcaption>{label}</figcaption><p class="none">Never applied.</p></figure>'
        )
    frame = evidence.frame
    image = _image(label, frame) if frame is not None else '<p class="none">No frame.</p>'
    url = f"<p>URL: <code>{_e(evidence.url)}</code></p>" if evidence.url is not None else ""
    snapshot = ""
    if evidence.snapshot is not None:
        snapshot = (
            f"<details><summary>Accessibility snapshot ({len(evidence.snapshot)} chars)</summary>"
            f"<pre>{_e(evidence.snapshot)}</pre></details>"
        )
    return f"<figure><figcaption>{label}</figcaption>{image}{url}{snapshot}</figure>"


def _image(label: str, frame: Frame) -> str:
    """Render `frame` inline as a data: URI, with its size and short digest beneath it."""
    data = base64.b64encode(frame.png).decode("ascii")
    size = f"{frame.width}x{frame.height}"
    return (
        f'<img src="data:{PNG_MEDIA_TYPE};base64,{data}" width="{frame.width}" '
        f'height="{frame.height}" alt="{label} screen, {size}">'
        f'<p class="meta">{size}, sha256 {frame.sha256[:DIGEST_CHARS]}, captured '
        f"{frame.captured_at.isoformat()}</p>"
    )


def _postconditions(checks: tuple[RecordedPostcondition, ...]) -> str:
    """Render the declared postconditions as a table, or say none were declared."""
    if not checks:
        return '<h3>Postconditions</h3><p class="none">None declared.</p>'
    rows = "".join(_postcondition_row(check) for check in checks)
    return (
        "<h3>Postconditions</h3><table><thead><tr><th>Kind</th><th>Subject</th>"
        f"<th>Expected</th><th>Held</th><th>Observed</th></tr></thead><tbody>{rows}</tbody></table>"
    )


def _postcondition_row(check: RecordedPostcondition) -> str:
    """Render one postcondition's table row; a failed one is marked so it stands out."""
    marked = ' class="failed"' if check.has_held is False else ""
    expected = check.expected if check.expected is not None else "-"
    return (
        f"<tr><td>{_e(check.kind)}</td><td>{_e(check.subject)}</td><td>{_e(expected)}</td>"
        f"<td{marked}>{_HELD_TEXT[check.has_held]}</td><td>{_e(check.observed or '-')}</td></tr>"
    )


def _definitions(facts: Sequence[tuple[str, str]]) -> str:
    """Render label/value pairs as a definition list, every value escaped."""
    items = "".join(f"<dt>{label}</dt><dd>{_e(value)}</dd>" for label, value in facts)
    return f"<dl>{items}</dl>"


def _e(text: str) -> str:
    """Escape recorded text for HTML, quotes included, so it can sit in content or an attribute."""
    return html.escape(text, quote=True)
