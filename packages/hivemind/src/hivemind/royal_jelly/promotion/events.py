"""Build the `tool.*` trail events a tool's life writes: one function per stage.

The Pheromone Trail (the Hive's append-only audit log) records every stage a requested tool goes
through (codingrules section 12 and Appendix C: every state edge is an event). This module is the
one place those events are minted: `tool_event` stamps the Hive, node and actor identity, a fresh
id and the injected clock's time on a `ToolEvent`, and the stage builders fix each kind's payload
so the trail reads the same wherever a stage is written. The Worker's tool invoker uses
`tool_event` too, for `tool.loaded` and `tool.load_refused`. The payload rule is the trail's own:
ids, counts, enum values, hashes and short reasons, never a tool's source, a prompt or a report's
output (the report is stored in the registry row, not on the trail).

Fits into the Hive:
    Layer 3 (sources of Cells, and capabilities handed down), inside royal_jelly.promotion. Called
    by `PromotionFlow` and by the Worker's tool invoker; calls into `hivemind.pheromone` and
    waggle's ids only.

Key invariants:
    - Every payload value is a scalar, and every string is cut to the trail's string bound, so a
      long reason can never make an event invalid.
    - Every event's subject is the tool's id, so one query for a tool id returns its whole story.
    - Ids and times come from the injected clock; nothing here reads the wall clock.

See Also:
    - hivemind.honey_store.identity for the same pattern on `honey.*` events.
    - hivemind.pheromone.events.families for `ToolEvent` and its kinds.
"""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import JsonValue

from hivemind.pheromone import ToolEvent
from hivemind.pheromone.events.base import MAX_PAYLOAD_STRING_CHARS
from hivemind.royal_jelly.registry import RegistryChange
from hivemind.royal_jelly.scaffold import ToolRequestBrief
from hivemind.royal_jelly.spec import QuarantineReport, ToolSpec
from waggle.clock import Clock
from waggle.ids import HiveId, NodeId, ToolId, new_event_id

SHA256_PREFIX_CHARS = 16  # A hash on the trail is recognisable by its first sixteen hex digits.
_TRIM = MAX_PAYLOAD_STRING_CHARS - 1  # One short of the trail's bound, so a reason always fits.

# One payload value: an id, a count, an enum's `.value`, a hash prefix or a flag.
type PayloadValue = str | int | float | bool | None

__all__ = [
    "SHA256_PREFIX_CHARS",
    "PayloadValue",
    "ToolIdentity",
    "load_refused_event",
    "loaded_event",
    "promoted_event",
    "quarantine_requested_event",
    "quarantined_event",
    "rejected_event",
    "requested_event",
    "scaffolded_event",
    "tool_event",
]


@dataclass(frozen=True, slots=True)
class ToolIdentity:
    """The Hive, node and actor stamped on every `tool.*` event one writer records."""

    hive_id: HiveId  # The Hive the events belong to.
    node_id: NodeId  # The node whose trail segment the events land on.
    actor: str  # A bee id, or the literal "human" or "system".


def tool_event(
    identity: ToolIdentity,
    clock: Clock,
    kind: str,
    tool_id: ToolId,
    /,
    **payload: PayloadValue,
) -> ToolEvent:
    """Mint one `ToolEvent` with a fresh id, the clock's time and a scalar payload.

    Args:
        identity: Which Hive, node and actor the event is recorded under.
        clock: The injected clock; supplies the event id's timestamp and `at`.
        kind: One of `ToolEvent`'s own kinds (`"tool.requested"`, ...).
        tool_id: The tool the event is about; becomes the event's subject.
        **payload: Ids, counts, hashes, enum values and flags; strings are cut to the trail bound.

    Returns:
        The validated event, ready for `PheromoneTrail.record`.

    Raises:
        pydantic.ValidationError: `kind` is not a tool.* kind or the actor is not a valid id.
    """
    # Copied into a JsonValue-typed dict (dict is invariant in its value type), cutting any
    # overlong string so a reason from a model or a sandbox can never invalidate the event.
    fields: dict[str, JsonValue] = {
        key: value[:_TRIM] if isinstance(value, str) else value for key, value in payload.items()
    }
    return ToolEvent(
        id=new_event_id(clock),
        hive_id=identity.hive_id,
        node_id=identity.node_id,
        at=clock.now(),
        actor=identity.actor,
        kind=kind,
        subject_id=tool_id,
        payload=fields,
    )


def requested_event(identity: ToolIdentity, clock: Clock, brief: ToolRequestBrief) -> ToolEvent:
    """Build `tool.requested`: the request entered the flow, with the ids and the reason."""
    return tool_event(
        identity,
        clock,
        "tool.requested",
        brief.tool_id,
        name=brief.name,
        scope=brief.scope.value,
        cell_id=brief.cell_id,
        requested_by=brief.requested_by,
        os=brief.platform.os.value,
        reason=brief.reason,
    )


def scaffolded_event(
    identity: ToolIdentity, clock: Clock, spec: ToolSpec, package: tuple[str, int, int]
) -> ToolEvent:
    """Build `tool.scaffolded`: a package exists, named by hash and size.

    Args:
        identity: The writer's identity.
        clock: The injected clock.
        spec: The scaffolded spec.
        package: `(package_sha256, size_bytes, attempt)`; the attempt is 1 or 2 (the retry).
    """
    digest, size, attempt = package
    return tool_event(
        identity,
        clock,
        "tool.scaffolded",
        spec.tool_id,
        name=spec.name,
        version=spec.version,
        package_sha256=digest[:SHA256_PREFIX_CHARS],
        size_bytes=size,
        attempt=attempt,
    )


def quarantine_requested_event(
    identity: ToolIdentity, clock: Clock, spec: ToolSpec, attempt: int
) -> ToolEvent:
    """Build `tool.quarantine_requested`: the package is being sent to a sandbox Comb."""
    return tool_event(
        identity,
        clock,
        "tool.quarantine_requested",
        spec.tool_id,
        name=spec.name,
        version=spec.version,
        scope=spec.scope.value,
        attempt=attempt,
    )


def quarantined_event(identity: ToolIdentity, clock: Clock, report: QuarantineReport) -> ToolEvent:
    """Build `tool.quarantined`: the Comb's verdict, with the failing gate and the path."""
    failing = next((gate for gate in report.gates if not gate.passed), None)
    return tool_event(
        identity,
        clock,
        "tool.quarantined",
        report.tool_id,
        name=report.name,
        version=report.version,
        passed=report.passed,
        failing_gate=report.failing_gate,
        reason=failing.reason if failing is not None else "",
        path=report.path.value,
        sandbox_cell_id=report.sandbox_cell_id,
        package_sha256=report.package_sha256[:SHA256_PREFIX_CHARS],
    )


def promoted_event(identity: ToolIdentity, clock: Clock, change: RegistryChange) -> ToolEvent:
    """Build `tool.promoted`: a version is now current in the Comb Registry."""
    row = change.row
    return tool_event(
        identity,
        clock,
        "tool.promoted",
        row.tool_id,
        name=row.name,
        version=row.version,
        scope=row.scope.value,
        cell_id=row.cell_id,
        package_sha256=row.package_sha256[:SHA256_PREFIX_CHARS],
        supersedes_version=change.supersedes_version,
        promoted_by=change.by,
        path=row.quarantine_path.value,
        reason=change.reason,
    )


def rejected_event(
    identity: ToolIdentity, clock: Clock, tool_id: ToolId, why: tuple[str, str, str]
) -> ToolEvent:
    """Build `tool.rejected`: the attempt ended, naming the stage, the code and the reason.

    Args:
        identity: The writer's identity.
        clock: The injected clock.
        tool_id: The rejected tool.
        why: `(stage, code, reason)`: `stage` is `scaffold`, `quarantine` or `promotion`; `code`
            is the failing gate's name or the refusal's stable code.
    """
    stage, code, reason = why
    return tool_event(
        identity, clock, "tool.rejected", tool_id, stage=stage, code=code, reason=reason
    )


def loaded_event(
    identity: ToolIdentity, clock: Clock, tool_id: ToolId, package: tuple[str, str, str]
) -> ToolEvent:
    """Build `tool.loaded`: a Worker's bundle gained a Comb tool and loaded its package.

    Args:
        identity: The loader's identity (a Worker is the actor).
        clock: The injected clock.
        tool_id: The loaded tool.
        package: `(name, version, package_sha256)`.
    """
    name, version, digest = package
    return tool_event(
        identity,
        clock,
        "tool.loaded",
        tool_id,
        name=name,
        version=version,
        package_sha256=digest[:SHA256_PREFIX_CHARS],
    )


def load_refused_event(
    identity: ToolIdentity, clock: Clock, tool_id: ToolId, why: tuple[str, str, str]
) -> ToolEvent:
    """Build `tool.load_refused`: a Worker refused to load a package, and why.

    Args:
        identity: The loader's identity (a Worker is the actor).
        clock: The injected clock.
        tool_id: The tool whose package was refused.
        why: `(name, code, reason)`: the tool's name, a stable code such as `hash_mismatch`, and
            one sentence.
    """
    name, code, reason = why
    return tool_event(
        identity, clock, "tool.load_refused", tool_id, name=name, code=code, reason=reason
    )
