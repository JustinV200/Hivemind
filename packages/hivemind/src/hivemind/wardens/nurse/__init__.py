"""Run the Nurse Bee on a model: pick an eligible JUDGE binding and review one attempt (ADR-0061).

The Nurse Bee is the Hive's fresh-context reviewer: after a Warden's deterministic acceptance
checks pass, a model that never saw the Worker's conversation reads the Warden-built evidence and
says PASS or what to fix. `hivemind.supervision.capping` holds the seam (`NurseRequest`,
`NurseReviewer`, `run_nurse`); this package is the model-backed side, split by job: how strong
the Worker was (`strength`), which JUDGE links may review it (`eligible`), the prompt (`prompt`),
the reply and its translation (`verdict`), and the reviewer that ties them together (`reviewer`).

Fits into the Hive:
    Layer 5 (per-Cell supervisors), inside `hivemind.wardens`. Used by the Warden's accept path
    and its composition root (later steps). Calls into `hivemind.llm`, `hivemind.forage`,
    `hivemind.memory`, `hivemind.pheromone`, `hivemind.supervision.capping` and waggle.

Key invariants:
    - Nothing here ever sees the Worker's transcript, hot state, Handoff, a proposal's reason or
      the claim's summary; NurseRequest cannot carry them.
    - A Nurse model is never weaker than the Worker it reviews; when none qualifies, the answer
      is UNAVAILABLE, never a weaker review.

See Also:
    - docs/adr/0061-the-nurse-bee-reviews-an-attempt-in-a-fresh-context.md for the decision.
    - hivemind.wardens.judge for the per-proposal judge this package mirrors.

Public API:
    - ModelNurseReviewer: the model-backed NurseReviewer (reviewer).
    - WorkerStrength, worker_strength: the Worker's grade and providers, spill included (strength).
    - CallSize, NurseChain, nurse_chain: the eligible JUDGE links for one review (eligible).
    - NURSE_OUTPUT_TOKENS, build_nurse_request: the one LLMRequest a review makes (prompt).
    - NURSE_RUBRIC_ID: the rubric id every Nurse verdict carries (verdict).
"""

from hivemind.wardens.nurse.eligible import CallSize, NurseChain, nurse_chain
from hivemind.wardens.nurse.prompt import NURSE_OUTPUT_TOKENS, build_nurse_request
from hivemind.wardens.nurse.reviewer import ModelNurseReviewer
from hivemind.wardens.nurse.strength import WorkerStrength, worker_strength
from hivemind.wardens.nurse.verdict import NURSE_RUBRIC_ID

__all__ = [
    "NURSE_OUTPUT_TOKENS",
    "NURSE_RUBRIC_ID",
    "CallSize",
    "ModelNurseReviewer",
    "NurseChain",
    "WorkerStrength",
    "build_nurse_request",
    "nurse_chain",
    "worker_strength",
]
