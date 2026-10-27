"""Run the Nurse Bee on a model: pick an eligible JUDGE binding and review one attempt (ADR-0061).

The Nurse Bee is the Hive's fresh-context reviewer: after a Warden's deterministic acceptance
checks pass, a model that never saw the Worker's conversation reads the Warden-built evidence and
says PASS or what to fix. `hivemind.supervision.capping` holds the seam (`NurseRequest`,
`NurseReviewer`, `run_nurse`); this package is the model-backed side, split by job: how strong
the Worker was (`strength`), which JUDGE links may review it (`eligible`), the prompt (`prompt`),
the reply and its translation (`verdict`), the reviewer that ties them together (`reviewer`), and
the accept path's side (step 4): what every bee of an attempt ran on and proposed (`trace`), the
touched files read through the Guard (`evidence`), and the wiring, request and trail event the
Warden uses (`accept`).

Fits into the Hive:
    Layer 5 (per-Cell supervisors), inside `hivemind.wardens`. Used by the Warden's accept path
    (`hivemind.wardens.ticks.results`), its spawn and respawn paths (the trace) and the
    composition roots (the wiring). Calls into `hivemind.cell`, `hivemind.guard.scanner`,
    `hivemind.wardens.acceptance`, `hivemind.llm`, `hivemind.forage`,
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
    - NurseTrace: every bee of one attempt, its binding and its gate (trace).
    - EvidenceReader, read_evidence, touched_paths, bee_may_read, spend_left: the files and
      budget (evidence).
    - NurseWiring, AttemptEvidence, JUDGED_KINDS, machine_report, unjudged_report, nurse_request,
      grant_calls, ask_nurse, not_nursed, nurse_event: the accept path's side (accept).
"""

from hivemind.wardens.nurse.accept import (
    JUDGED_KINDS,
    AttemptEvidence,
    NurseWiring,
    ask_nurse,
    grant_calls,
    machine_report,
    not_nursed,
    nurse_event,
    nurse_request,
    unjudged_report,
)
from hivemind.wardens.nurse.eligible import CallSize, NurseChain, nurse_chain
from hivemind.wardens.nurse.evidence import (
    EvidenceReader,
    bee_may_read,
    read_evidence,
    spend_left,
    touched_paths,
)
from hivemind.wardens.nurse.prompt import NURSE_OUTPUT_TOKENS, build_nurse_request
from hivemind.wardens.nurse.reviewer import ModelNurseReviewer
from hivemind.wardens.nurse.strength import WorkerStrength, worker_strength
from hivemind.wardens.nurse.trace import NurseTrace
from hivemind.wardens.nurse.verdict import NURSE_RUBRIC_ID

__all__ = [
    "JUDGED_KINDS",
    "NURSE_OUTPUT_TOKENS",
    "NURSE_RUBRIC_ID",
    "AttemptEvidence",
    "CallSize",
    "EvidenceReader",
    "ModelNurseReviewer",
    "NurseChain",
    "NurseTrace",
    "NurseWiring",
    "WorkerStrength",
    "ask_nurse",
    "bee_may_read",
    "build_nurse_request",
    "grant_calls",
    "machine_report",
    "not_nursed",
    "nurse_chain",
    "nurse_event",
    "nurse_request",
    "read_evidence",
    "spend_left",
    "touched_paths",
    "unjudged_report",
    "worker_strength",
]
