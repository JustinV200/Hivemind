"""Run a CELL-scope tool request as this Warden's own promotion flow (roadmap 9.6, ADR-0046).

A tool only one machine's bees need is promoted by that machine's Warden, not the Queen. When a
sub-bee's `tool.request` asks for CELL scope, the relay hands it here. The checks that need no
model run at once, inside the tick: the asker holds `tool:request`, holds every capability it
asks the tool to need, and this Warden's own set allows `tool:scope:cell`; and a model slot to
write the tool exists (the first of the grant's allowed bindings that is the SCAFFOLDER slot and
passes the Guard's `slot_binding` point, resolved through `deps.rebind`, otherwise the asker is
answered `hive.tool.no_scaffolder`). Everything that waits -- the scaffolding model call, the
Queen's sandbox, the promotion -- is `PromotionFlow.run` in a background job the Warden owns, whose
ports are this package's (`ports`, `remote_registry`) over the Warden's own Basket. The flow
promotes in process through the Hive Stand's registry or, in a Cell, announces to the Queen; a
rejection is answered to the asker as `hive.tool.rejected`, a success by the announcer.

Fits into the Hive:
    Layer 5 (per-Cell supervisors; spawn and supervise Workers), inside the wardens package's
    ticks.tools sub-package. Called by `relay` for a CELL-scope request. Calls into
    `hivemind.royal_jelly` (the flow, the scaffolder, the principal), `hivemind.wardens.spawn`
    (the binding check), `hivemind.llm` (the scaffolder's gate type, via deps) and this package.

Key invariants:
    - The Warden promotes only for its own Cell and only as its own `WardenPrincipal`.
    - The scaffolder's binding is one this Warden's grant allows and the Guard passed; nothing
      here ever picks a model by name.
    - The tick never awaits the flow: it starts a job and returns.
    - Whatever way the flow ends, the asker is answered exactly once.

See Also:
    - docs/adr/0046-tool-spec-comb-registry-and-scopes.md for the two scopes and their promoters.
    - hivemind.royal_jelly.promotion.flow for the flow this runs.
    - hivemind.wardens.ticks.tools.ports for the ports it is given.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from pydantic import ValidationError

from hivemind.common.errors import HiveMindError
from hivemind.common.logging import get_logger
from hivemind.forage.slots import ModelSlot
from hivemind.forage.tempo import Tempo
from hivemind.guard import (
    Capability,
    CapabilityFamily,
    InvalidCapabilityError,
    PolicyContext,
    warden_principal,
)
from hivemind.llm import BoundModel, UnknownProviderError, UnresolvableSlotError
from hivemind.manifest.schema.royal_jelly import DEFAULT_MAX_RETRIES, DEFAULT_SANDBOX_DISTRIBUTION
from hivemind.royal_jelly import (
    ModelScaffolder,
    PromotionFlow,
    PromotionPorts,
    Scaffolder,
    ToolRequestBrief,
    ToolState,
    WardenPrincipal,
    decide_path,
)
from hivemind.wardens.spawn import BindingCheck, authorize_binding
from hivemind.wardens.ticks.basket.jobs import start_job
from hivemind.wardens.ticks.tools.messages import (
    NO_SCAFFOLDER,
    REJECTED,
    REQUEST_REFUSED,
    answer_asker,
    tool_error,
    tool_identity,
)
from hivemind.wardens.ticks.tools.pending import Asker
from hivemind.wardens.ticks.tools.ports import WardenAnnouncer, WardenHoney, WardenSandboxRunner
from hivemind.wardens.ticks.tools.remote_registry import RemoteCombRegistry
from waggle.errors import WaggleError
from waggle.messages.control import ErrorMessage
from waggle.messages.forage import GrantIssued
from waggle.messages.reports import PlatformReport
from waggle.messages.tool import ToolRequest
from waggle.messages.tool.authoring import QuarantinePath

if TYPE_CHECKING:
    from hivemind.wardens.spawn.sub_bee import SubBee
    from hivemind.wardens.warden import Warden

_TOOL_REQUEST = Capability(family=CapabilityFamily.TOOL_REQUEST)  # Asking for a tool at all.
_SCOPE_CELL = Capability(family=CapabilityFamily.TOOL_SCOPE, scope="cell")  # Promoting for a Cell.

__all__ = ["start_cell_scope"]

log = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class _Started:
    """What `_prepare` produced for an accepted request: the flow and its brief."""

    flow: PromotionFlow
    brief: ToolRequestBrief


async def start_cell_scope(warden: Warden, request: ToolRequest, asker: Asker) -> None:
    """Refuse a CELL-scope request now, or start this Warden's promotion flow for it.

    Args:
        warden: The promoting Warden; it must hold a Cell (the relay checked the request's Cell).
        request: The asker's request, its identity already checked by the relay.
        asker: The sub-bee that asked and its own envelope id.
    """
    sub_bee = warden._sub_bees.get(asker.worker_id)
    if sub_bee is None or warden._cell is None:
        return  # The asker ended meanwhile: nobody is left to answer.
    prepared = await _prepare(warden, request, asker, sub_bee)
    if isinstance(prepared, ErrorMessage):
        await answer_asker(warden, asker.worker_id, prepared, asker.original_id)
        return
    start_job(warden, _run(warden, prepared, asker))


async def _prepare(
    warden: Warden, request: ToolRequest, asker: Asker, sub_bee: SubBee
) -> _Started | ErrorMessage:
    """Check the request and assemble the flow, or return the error that answers the asker."""
    refusal = _capability_refusal(warden, request, sub_bee)
    if refusal is not None:
        return tool_error(REQUEST_REFUSED, refusal)
    try:
        brief = ToolRequestBrief.from_request(request)
    except ValidationError:
        return tool_error(
            REQUEST_REFUSED, f"The tool request {request.tool_id} is not a valid brief."
        )
    scaffolder = await _scaffolder(warden, sub_bee)
    if scaffolder is None:
        message = "No model slot to write a tool is allowed by this Warden's grant."
        return tool_error(NO_SCAFFOLDER, message)
    deps = warden._deps
    access = deps.royal_jelly
    ports = PromotionPorts(
        scaffolder=scaffolder,
        runner=WardenSandboxRunner(warden, request.task_id, request.reason),
        registry=access.registry if access is not None else RemoteCombRegistry(warden),
        honey=WardenHoney(warden),
        announcer=WardenAnnouncer(warden, asker),
        path_for=lambda platform: _path_for(warden, platform),
    )
    retries = access.config.max_retries if access is not None else DEFAULT_MAX_RETRIES
    flow = PromotionFlow(ports, deps.trail, tool_identity(deps), deps.clock, max_retries=retries)
    return _Started(flow, brief)


def _capability_refusal(warden: Warden, request: ToolRequest, sub_bee: SubBee) -> str | None:
    """Return why the asker may not request this CELL-scope tool, or None when it may."""
    if not sub_bee.capabilities.allows(_TOOL_REQUEST):
        return (
            f"Sub-bee {sub_bee.worker_id} does not hold tool:request, so it cannot ask for a tool."
        )
    if not warden._ceiling.allows(_SCOPE_CELL):
        return "This Warden does not hold tool:scope:cell, so it cannot promote for its Cell."
    for text in request.required_capabilities:
        try:
            needed = Capability.parse(text)
        except InvalidCapabilityError:
            return f"The required capability {text!r} is not a valid capability string."
        # A tool never needs more than the bee that asked for it holds (ADR-0046).
        if not sub_bee.capabilities.allows(needed):
            return f"Sub-bee {sub_bee.worker_id} does not hold {text!r}, so no tool may need it."
    return None


async def _scaffolder(warden: Warden, sub_bee: SubBee) -> Scaffolder | None:
    """Return a scaffolder on a SCAFFOLDER binding the grant allows and the Guard passes."""
    deps, cell = warden._deps, warden._cell
    grant = _grant_of(warden, sub_bee)
    if grant is None or cell is None:
        return None
    if not any(ModelSlot.from_wire(b.slot) is ModelSlot.SCAFFOLDER for b in grant.allowed):
        return None
    key = ModelSlot.SCAFFOLDER.manifest_key
    context = PolicyContext(
        comb_shield=cell.comb_shield, access_level=cell.access_level, bound_tier=cell.comb_shield
    )
    check = BindingCheck(
        orderer=warden_principal(warden._warden_id),
        capabilities=warden._ceiling,
        grant=grant,
        binding_key=key,
        slot=ModelSlot.SCAFFOLDER,
        context=context,
    )
    if not (await authorize_binding(deps, check)).allowed:
        return None  # Already a guard.denied row; the asker is told no slot is allowed.
    try:
        bound = deps.rebind(key)
    except (UnresolvableSlotError, UnknownProviderError):
        log.warning("wardens.tool.scaffolder_unbound", warden_id=warden._warden_id)
        return None
    assignment = sub_bee.assignment
    gate = deps.lane_for_grant(
        grant.grant_id, assignment.goal_id, Tempo.from_wire(assignment.tempo)
    )
    return ModelScaffolder(_serving_scaffolder(bound), gate, deps.clock)


def _serving_scaffolder(bound: BoundModel) -> BoundModel:
    """Return `bound` (and its fallbacks) recorded as serving the SCAFFOLDER slot.

    `deps.rebind` resolves a named binding as the WORKER slot (its one use is moving a sub-bee to
    a stronger binding), so the scaffold request would otherwise be sent, metered and traced as a
    WORKER call although it runs on the scaffolder's row.
    """
    fallback = _serving_scaffolder(bound.fallback) if bound.fallback is not None else None
    return replace(bound, slot=ModelSlot.SCAFFOLDER, fallback=fallback)


def _grant_of(warden: Warden, sub_bee: SubBee) -> GrantIssued | None:
    """Return the grant the asker runs under, else the newest grant this Warden holds."""
    grants = warden._grants
    return grants.get(sub_bee.assignment.grant_id) or next(reversed(grants.values()), None)


def _path_for(warden: Warden, platform: PlatformReport) -> QuarantinePath:
    """Decide the quarantine route for a target platform with the sandbox image's distribution."""
    access = warden._deps.royal_jelly
    distribution = access.config.sandbox_distribution if access else DEFAULT_SANDBOX_DISTRIBUTION
    return decide_path(platform, distribution)


async def _run(warden: Warden, started: _Started, asker: Asker) -> None:
    """Background job: run the flow, and answer the asker with an error unless it was promoted."""
    cell = warden._cell
    if cell is None:
        return
    principal = WardenPrincipal(warden._warden_id, cell.id)
    brief = started.brief
    try:
        outcome = await started.flow.run(brief, principal)
    except (HiveMindError, WaggleError, OSError, TimeoutError) as exc:
        log.warning("wardens.tool.flow_failed", tool_id=brief.tool_id, error=repr(exc))
        failure = f"The tool {brief.name} could not be built: {type(exc).__name__}."
        await answer_asker(
            warden,
            asker.worker_id,
            tool_error(REJECTED, failure, retryable=True),
            asker.original_id,
        )
        return
    if outcome.state is ToolState.PROMOTED:
        return  # The announcer already told the asker.
    text = f"The tool {brief.name} was not promoted ({outcome.reason})."
    await answer_asker(warden, asker.worker_id, tool_error(REJECTED, text), asker.original_id)
