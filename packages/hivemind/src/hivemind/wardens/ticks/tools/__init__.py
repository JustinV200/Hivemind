"""Provide the Warden's Royal Jelly tick handlers: relay tool requests, promote, install, prove.

The Royal Jelly Lab is the Hive's tool-authoring pipeline: a bee asks for a tool it lacks, the Lab
scaffolds it, the Quarantine Comb proves it in a sandbox and the Comb Registry promotes it
(ADR-0046, ADR-0047). A Warden's part is five things, one module each. `relay` carries a
sub-bee's `tool.request` up to the Queen (HIVE scope) and her answer back, or starts the Warden's
own flow (CELL scope);
`cell_scope` assembles that flow over the Warden's own ports (`ports`: sandbox, Honey, announcement;
`remote_registry`: a Cell's way to promote through the Queen); `install` makes a promoted tool
available to the sub-bees allowed to call it; `quarantine_run` runs the Comb on this Cell when the
Queen orders it; and `seams` wires all of it into `spawn_sub_bee`. `pending` holds what the Warden
is waiting on and `messages` builds the errors and announcements it sends.

Fits into the Hive:
    Layer 5 (per-Cell supervisors; spawn and supervise Workers), inside the wardens package's
    ticks sub-package. Called by `hivemind.wardens.ticks.dispatch.act` (`FORWARD_TOOL`,
    `RUN_QUARANTINE`) and by `hivemind.wardens.ticks.assign` and `.alarms` (`comb_seams`).

Key invariants:
    - A request is relayed or fulfilled only for the sub-bee that sent it; the Warden promotes
      only for its own Cell, as its own `WardenPrincipal`.
    - A tool reaches a sub-bee only after its package is resident and verified, and only if the
      sub-bee's own capability set allows it.
    - Nothing in a tick waits on a model, a sandbox or the Queen: those are background jobs
      resolved by later ticks.

See Also:
    - docs/adr/0046-tool-spec-comb-registry-and-scopes.md for the registry and its scopes.
    - docs/adr/0047-quarantine-comb-gates-and-sandbox.md for the Comb and its sandbox.
    - hivemind.wardens.ticks.basket for the Basket handlers these depend on.

Public API (roadmap steps 9.6 and 9.7):
    - handle_tool_item (relay): the `FORWARD_TOOL` action's one entry point.
    - ToolRelay, Asker (pending): what the Warden is waiting on.
    - install_tool, reattenuate (install): hand a promoted tool to the bees that may call it.
    - start_cell_scope (cell_scope): the Warden's own CELL-scope flow.
    - handle_quarantine_order (quarantine_run): run the Comb for a Queen-sent order.
    - comb_seams (seams): a sub-bee's Comb tools at spawn.
    - RemoteCombRegistry (remote_registry), the ports in `ports`, and `messages`.
"""

from hivemind.wardens.ticks.tools.cell_scope import start_cell_scope
from hivemind.wardens.ticks.tools.install import install_tool, reattenuate
from hivemind.wardens.ticks.tools.pending import Asker, ToolRelay
from hivemind.wardens.ticks.tools.quarantine_run import handle_quarantine_order
from hivemind.wardens.ticks.tools.relay import handle_tool_item
from hivemind.wardens.ticks.tools.remote_registry import RemoteCombRegistry
from hivemind.wardens.ticks.tools.seams import comb_seams

__all__ = [
    "Asker",
    "RemoteCombRegistry",
    "ToolRelay",
    "comb_seams",
    "handle_quarantine_order",
    "handle_tool_item",
    "install_tool",
    "reattenuate",
    "start_cell_scope",
]
