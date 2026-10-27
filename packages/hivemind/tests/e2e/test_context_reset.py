"""End-to-end: a real Drone whose context crosses the line mid-loop is handed off and finishes.

A Drone records each model round's usage the moment it lands, so the context it reports is the one
it holds now, mid-loop. This module runs the whole Hive Stand -- the Queen, the Hive Stand's own
Warden and a real Drone over one scripted `FakeLLMProvider`, real SQLite, a real child process --
where the Drone's first round alone fills its context, and nobody pulls a lever:

- past the handoff threshold, the Drone checkpoints and resets itself before it runs the call that
  round asked for (codingrules 8.9: a bee "checkpoints and resets itself");
- between the compact line and the threshold, the bee's own check stays quiet; its Warden reads the
  figure off a Heartbeat while a one-second command runs and orders a compact, which the bee
  carries out at its next tool call ("its supervisor may order it earlier").

Either way the episode that resumes from the Handoff writes the three haiku and the goal succeeds.
Usage used to be recorded once the tool loop had ended: neither check ever saw the crossing, and
the first episode ran on to its end holding a full window.

Fits into the Hive:
    Test infrastructure (codingrules section 14.2), not shipped.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.workers.roles.bounded_loop.runner for `_MeteredGate`, the per-round record this
      proves.
    - hivemind.wardens.ticks.heartbeat for check_sub_bee_context, the Warden's own context check.
    - tests.e2e.test_handoff_successor for the Warden's Handoff lever pulled by hand.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest
from builders.cli import ManifestTuning, fake_manifest
from e2e.kernel_helpers import (
    HaikuScript,
    WorkerTurn,
    default_worker_turn,
    text_response,
    tool_response,
    tool_round_count,
    write_call,
)

from hivemind.cell import HoneyClearance
from hivemind.cli.compose import Hive, build_hive, run_goal, run_hive
from hivemind.llm import LLMRequest, LLMResponse, ProviderCapabilities, TextPart, Usage
from hivemind.manifest import load_manifest
from hivemind.pheromone import TrailQuery
from waggle.clock import SystemClock

pytestmark = pytest.mark.e2e

_GOAL = "write three haiku about bees to separate files"
_TIMEOUT_S = 20.0  # Generous: one busy second, a checkpoint and a resumed episode.
_BUSY = [sys.executable, "-c", "import time; time.sleep(1)"]  # Long enough for a Warden's order.
_FILES = ("haiku_1.txt", "haiku_2.txt", "haiku_3.txt")
# hivemind.memory.hot_state.packing's own closing delimiter: the system prompt names the opening
# one in its instructions, so only a Handoff actually rendered into the prompt carries this.
_HANDOFF_BLOCK = "<<<end handoff>>>"
_OWN_THRESHOLD = 0.9  # Past the manifest's handoff threshold (two thirds): the bee's own reset.
_WARDENS_COMPACT = 0.55  # Past the Warden's compact line (half), short of the bee's own threshold.
_FULL = ManifestTuning(hive_stand_full=True)


@pytest.mark.parametrize("capabilities", ("full", "none"))
@pytest.mark.parametrize(
    "fraction", (_OWN_THRESHOLD, _WARDENS_COMPACT), ids=("own_threshold", "wardens_compact")
)
def test_a_drone_whose_context_crosses_the_line_mid_loop_is_handed_off_and_finishes(
    tmp_path: Path, capabilities: str, fraction: float
) -> None:
    resumed: list[bool] = []
    script = HaikuScript(_filling_round(_window(capabilities), fraction, resumed))
    hive = build_hive(
        # FULL: the busy second is a command, which a Hive Stand below FULL refuses (ADR-0060).
        load_manifest(fake_manifest(tmp_path, capabilities=capabilities, tuning=_FULL), {}),
        environ={},
        clock=SystemClock(),
        responders={"fake": script.responder},
    )

    asyncio.run(_run(hive, resumed, reset_in_place=fraction == _WARDENS_COMPACT))


def _window(capabilities: str) -> int:
    """The fake binding's context window at `capabilities` (builders.cli's own two shapes)."""
    full = capabilities == "full"
    shape = ProviderCapabilities.full() if full else ProviderCapabilities.none()
    return shape.context_window


def _filling_round(window: int, fraction: float, resumed: list[bool]) -> WorkerTurn:
    """The first episode's first round fills `fraction` of the window; a resumed one finishes."""

    def worker_turn(request: LLMRequest) -> LLMResponse:
        if _resumes(request):
            resumed.append(True)
            return default_worker_turn(request)
        rounds = tool_round_count(request)
        if rounds == 0:
            busy = tool_response(request, (("busy", "run_command", {"argv": _BUSY}),))
            usage = Usage(input_tokens=int(window * fraction), output_tokens=0)
            return busy.model_copy(update={"usage": usage})
        if rounds == 1:
            # Reached only if nothing handed the episode off: it would finish the goal unreset.
            return tool_response(request, tuple(write_call(name) for name in _FILES))
        return text_response("Three haiku written.")

    return worker_turn


def _resumes(request: LLMRequest) -> bool:
    """Whether `request` belongs to an episode resuming from a Handoff (its prompt carries one)."""
    texts = [request.system or ""]
    texts += [part.text for m in request.messages for part in m.parts if isinstance(part, TextPart)]
    return any(_HANDOFF_BLOCK in text for text in texts)


async def _run(hive: Hive, resumed: list[bool], *, reset_in_place: bool) -> None:
    """Run the goal; check the crossing alone checkpointed it and a resumed episode finished."""
    async with run_hive(hive):
        report = await run_goal(hive, _GOAL, clearance=HoneyClearance.C1, timeout_s=_TIMEOUT_S)
    kinds = [event.kind for event in await hive.stores.trail.query(TrailQuery())]
    assert report.succeeded, report
    assert "worker.handing_off" in kinds, kinds
    assert "memory.checkpoint" in kinds, kinds
    assert resumed, "No episode ever resumed from the first one's Handoff."
    assert "alarm.raised" not in kinds, kinds
    if reset_in_place:
        # A compact carries on in the same bee: its Warden's order, once, and no successor bee.
        assert "worker.resumed" in kinds, kinds
        assert kinds.count("worker.spawned") == 1, kinds
