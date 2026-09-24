"""Run the composed Hive's own Guard Bee in a test: quick rounds, and a judge that answers it.

`build_hive` wires the Guard Bee in (roadmap step 10.6), on `[guard.bee] interval_s` (5 s by
default). `quick_rounds` appends a `[guard.bee]` table to an already-written manifest so a test's
Guard Bee reads the trail about every Queen tick. `GuardReviews` wraps a test's responder: a
Guard review (an awake episode on the judge slot, recognised by its prompt's own title) gets the
verdict the test chose, in the shape the rung it arrived on expects, and every other call goes to
the wrapped responder unchanged.

Fits into the Hive:
    Test infrastructure (codingrules section 14.5), not shipped. Used through `builders.guard_bee`
    by the tests that run the composed Hive's Guard Bee end to end.

Key invariants:
    - Only a request whose system prompt carries the Guard review's title is answered here.
"""

from __future__ import annotations

import json
from pathlib import Path

from hivemind.forage import ModelSlot
from hivemind.llm import LLMRequest, LLMResponse, Responder, text_response

__all__ = ["GUARD_REVIEW_TITLE", "GuardReviews", "quick_rounds"]

GUARD_REVIEW_TITLE = "The Guard Bee's Judge"  # The first heading of the guard_review prompt.
QUICK_ROUND_S = 0.05  # About one Queen tick in the fake manifests' cadence.


def quick_rounds(manifest_path: Path, interval_s: float = QUICK_ROUND_S) -> Path:
    """Append `[guard.bee] interval_s` to a written manifest, and return its path.

    Args:
        manifest_path: A manifest `fake_manifest` (or one built on it) wrote, with no
            `[guard.bee]` table of its own.
        interval_s: Seconds between two Guard Bee rounds.

    Returns:
        `manifest_path`, for chaining into `load_manifest`.
    """
    text = manifest_path.read_text(encoding="utf-8")
    manifest_path.write_text(f"{text}\n[guard.bee]\ninterval_s = {interval_s}\n", encoding="utf-8")
    return manifest_path


class GuardReviews:
    """A responder that answers every Guard review with one verdict and counts them."""

    def __init__(self, inner: Responder, confidence: str, action: str) -> None:
        """Answer Guard reviews with `confidence` and `action`; everything else goes to `inner`.

        Args:
            inner: The test's own responder, for every call that is not a Guard review.
            confidence: The verdict's confidence (`low` to `critical`).
            action: The verdict's action (`observe` or a request its targets allow).
        """
        self._inner = inner
        self._body = json.dumps({"confidence": confidence, "action": action})
        self.reviews = 0

    def __call__(self, request: LLMRequest) -> LLMResponse:
        """Answer one model call: a Guard review with the verdict, anything else as before."""
        if request.slot is not ModelSlot.JUDGE or GUARD_REVIEW_TITLE not in (request.system or ""):
            return self._inner(request)
        self.reviews += 1
        # A schema travels natively or in JSON mode; the prompted rung expects one fenced block.
        if request.response_schema is not None:
            return text_response(self._body)
        return text_response(f"```json\n{self._body}\n```")
