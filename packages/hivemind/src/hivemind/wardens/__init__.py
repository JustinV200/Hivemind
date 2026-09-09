"""Represent the Warden: the per-Cell supervisor that spawns and supervises Workers on one Cell.

It covers Warden state, its inbox, its Autopilot (which never awaits a model) and Awake (a
bounded, stateless episode where it may think with a model) modes, spawning, its local model
pool, offline handling and read-only Watch mode (observing its Cell on a schedule instead of
sitting idle); a Warden never provisions Cells itself. Roadmap step 3.19 builds the Warden proper
(`Warden`, `WardenDeps`, `WardenState`, autopilot, awake, spawn, inbox, local_pool) and the
Warden-side half of acceptance criteria (step 3.18: `run_acceptance` runs a task's acceptance
postconditions on the Warden's own session, never the sub-bee's, before the task may reach
SUCCEEDED); the planner-side half (emitting acceptance criteria for every subtask) is step 3.20's,
in `hivemind.queen`.

Fits into the Hive:
    Layer 5 (per-Cell supervisors; spawn and supervise Workers). Called by queen's dispatcher,
    which assigns tasks to a Warden. Calls into workers (Layer 4) and everything below it.

Key invariants:
    - Every `WardenState` change goes through `hivemind.wardens.state.assert_transition` and is
      followed, in the same call, by its own `warden.*` trail event (codingrules section 12).
    - A task reaches SUCCEEDED only after its Warden -- never the bee that did the work -- has run
      `run_acceptance` and every criterion held (codingrules section 8.12; roadmap step 3.18).
    - `hivemind.wardens.autopilot` never imports `hivemind.llm`, directly or transitively
      (codingrules section 4; `lint-imports` enforces it).
    - A Warden never provisions a Cell: it only leases, opens a session and releases, through its
      injected `hivemind.cell.RealCellSource` (CLAUDE.md: "Wardens never provision Cells").

See Also:
    - .claude/codingrules.md section 4 for the layer 5 row this package occupies.
    - .claude/codingrules.md section 8.8 for the Warden's own shape: state, Attendant, autopilot,
      awake, spawn, local pool.
    - .claude/codingrules.md section 8.12 for "the proposer never verifies its own work".
    - docs/adr/0012-wardens-alarms-and-the-escalation-chain.md for the escalation chain this
      package's `ticks.alarms` implements the Warden's own hop of.
    - .claude/roadmap.md phase 3 steps 3.18 and 3.19 for this package's own roadmap bullets.
    - hivemind.wardens.README for the module-by-module map of this package.

Public API (roadmap steps 3.18, 3.19):
    - WardenState, TRANSITIONS, can_transition, assert_transition, is_terminal: the Warden state
      machine (state).
    - WardenDeps: every collaborator one Warden is built with (deps).
    - Warden: the TickLoop face itself, also a Supervisor over its sub-bees (warden).
    - AcceptanceReport, run_acceptance: the Warden-side half of acceptance criteria (acceptance).
    - CellRequestInputs, ForageRequestInputs, ToolRequestInputs, cell_request, forage_request,
      tool_request: the three requests a Warden sends the Queen when it needs something beyond
      its grant (requests).
    - WardenError, InvalidWardenTransitionError, LocalPoolExhaustedError, UnknownSubBeeError:
      this subsystem's error tree (errors).
    - WardenAction, SubBeeView, decide: the autopilot dispatch table (autopilot).
    - WardenDecision, decide_awake: one stateless awake episode (awake).
    - SubBee, WardenCellContext, spawn_sub_bee: starting a new sub-bee (spawn).
    - to_inbox_item, warden_attendant: the Warden's own Attendant (inbox).
    - LocalPool: a bare sub-bee-slot counter against a grant (local_pool).
"""

from hivemind.wardens.acceptance import AcceptanceReport, run_acceptance
from hivemind.wardens.autopilot import SubBeeView, WardenAction, decide
from hivemind.wardens.awake import WardenDecision, decide_awake
from hivemind.wardens.deps import WardenDeps
from hivemind.wardens.errors import (
    InvalidWardenTransitionError,
    LocalPoolExhaustedError,
    UnknownSubBeeError,
    WardenError,
)
from hivemind.wardens.inbox import to_inbox_item, warden_attendant
from hivemind.wardens.local_pool import LocalPool
from hivemind.wardens.requests import (
    CellRequestInputs,
    ForageRequestInputs,
    ToolRequestInputs,
    cell_request,
    forage_request,
    tool_request,
)
from hivemind.wardens.spawn import SubBee, WardenCellContext, spawn_sub_bee
from hivemind.wardens.state import (
    TRANSITIONS,
    WardenState,
    assert_transition,
    can_transition,
    is_terminal,
)
from hivemind.wardens.warden import Warden

__all__ = [
    "TRANSITIONS",
    "AcceptanceReport",
    "CellRequestInputs",
    "ForageRequestInputs",
    "InvalidWardenTransitionError",
    "LocalPool",
    "LocalPoolExhaustedError",
    "SubBee",
    "SubBeeView",
    "ToolRequestInputs",
    "UnknownSubBeeError",
    "Warden",
    "WardenAction",
    "WardenCellContext",
    "WardenDecision",
    "WardenDeps",
    "WardenError",
    "WardenState",
    "assert_transition",
    "can_transition",
    "cell_request",
    "decide",
    "decide_awake",
    "forage_request",
    "is_terminal",
    "run_acceptance",
    "spawn_sub_bee",
    "to_inbox_item",
    "tool_request",
    "warden_attendant",
]
