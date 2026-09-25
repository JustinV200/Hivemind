"""Implement keep: propose moving a scratch file to a path outside it, via a COPY action.

Roadmap step 5.0e: `keep(source, destination)` is an ordinary `outside_scratch_write` through the
same Capping gate, leave policy and Leavings ledger every other outside-scratch write goes
through (`hivemind.workers.tools.proposals.cap`) -- the only thing new is the action's own shape.
A unified diff cannot carry a binary well (or at all, past `waggle.messages.capping.action.
MAX_DIFF_CHARS`), so this tool builds a `waggle.messages.capping.ActionKind.COPY` action instead:
`source` and `destination` as `paths`, and the source's sha256/size, never its bytes -- the gate
reads `source`'s bytes from scratch itself at apply time and verifies the hash before writing
`destination` (`hivemind.supervision.capping.apply._apply_copy`), so this tool call never puts a
file's content on the wire twice. On a verified apply the source is removed from scratch (`keep`
moves a file, it does not duplicate it); on a DENY (or ASK-then-discard) verdict the write is
still applied and then restored on release, exactly like any other outside-scratch write the leave
policy does not persist -- `describe()`'s own leave-decision line (`hivemind.workers.tools.
proposals`) is what tells the model plainly whether the file will actually remain. Roadmap step
10.3a: the gate checks the destination's `fs:write` is held, so the tool first asks the Guard's
floors alone, and a destination that is the Hive's own state is refused as `guard.denied` before
anything is read or proposed (ADR-0041).

Fits into the Hive:
    Layer 4 (roles that do the work), inside `hivemind.workers.tools`. Registered by
    `hivemind.workers.tools.registry.build_registry`. Calls into `hivemind.cell`, `hivemind.llm`
    (JsonObject, ToolDefinition), `hivemind.guard` (the destination's floor check),
    `hivemind.supervision.capping` (RiskTier), `hivemind.workers.tools.authorize`,
    `hivemind.workers.tools.errors`, `hivemind.workers.tools.proposals`,
    `hivemind.workers.tools.registry` and waggle only.

Key invariants:
    - `source` must resolve inside this Worker's scratch directory once joined and resolved (the
      same `Path.resolve(strict=False)` `hivemind.workers.tools.session` already uses, which also
      catches a symlink that resolves outside scratch); refused otherwise, never proposed.
    - `destination` must expand (a leading `~`) to an absolute path that resolves outside scratch;
      refused otherwise. A destination inside scratch is not a `keep` at all -- the file is
      already there.
    - `keep` never reads `ctx.capabilities` or `ctx.lease` directly (mirrors `hivemind.workers.
      tools.session`'s own tools): the gate is the one place a rejection is decided.

See Also:
    - .claude/roadmap.md step 5.0e for this tool's own requirement, verbatim.
    - hivemind.workers.tools.proposals for make_proposal, cap and describe.
    - hivemind.supervision.capping.apply for _apply_copy, this tool's own effectful edge.
    - hivemind.workers.tools.session for read_file/write_file, the shape this tool's own
      source/destination resolution mirrors.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from hivemind.cell import PathNotAllowedError
from hivemind.guard import Capability, CapabilityFamily, EnforcementPoint
from hivemind.llm import JsonObject, ToolDefinition
from hivemind.supervision.capping import RiskTier
from hivemind.workers.tools.authorize import floor_refusal_text
from hivemind.workers.tools.errors import UnreachablePathError
from hivemind.workers.tools.proposals import ProposalRequest, cap, describe, make_proposal
from hivemind.workers.tools.registry import ToolInvocation, ToolSpec
from waggle.messages.capping import ActionKind, ProposedAction
from waggle.messages.labels import Postcondition, PostconditionKind

KEEP_DEFINITION = ToolDefinition(
    name="keep",
    description=(
        "Move a file from this Worker's scratch to a path outside it, proposed through the "
        "Capping gate before it happens. Use only for a path the brief's paths-to-remain block "
        "names; anything else stays in scratch and is removed when this task's lease releases, "
        "whatever this tool's own result says."
    ),
    parameters={
        "type": "object",
        "properties": {
            "source": {"type": "string"},
            "destination": {"type": "string"},
        },
        "required": ["source", "destination"],
        "additionalProperties": False,
    },
)

__all__ = ["KEEP_DEFINITION", "KEEP_SPEC", "keep"]


async def keep(invocation: ToolInvocation, arguments: JsonObject) -> str:
    """Propose moving `arguments["source"]` to `arguments["destination"]` and report the verdict.

    Args:
        invocation: This attempt's context and assignment.
        arguments: `source` (required, relative to scratch when relative, must resolve inside
            scratch) and `destination` (required, absolute or `~`-rooted, must resolve outside
            scratch).

    Returns:
        A readable string for malformed arguments, an unreachable source or a destination that
        does not leave scratch; otherwise `hivemind.workers.tools.proposals.describe`'s rendering
        of the gate's outcome, including whether the destination will actually remain.

    Raises:
        hivemind.workers.tools.errors.UnreachablePathError: `source` is outside scratch and
            outside every path this Worker's session can reach at all (a session/lease boundary,
            not a missing capability).
    """
    source = arguments.get("source")
    destination = arguments.get("destination")
    if not isinstance(source, str) or not source:
        return "source must be a non-empty string."
    if not isinstance(destination, str) or not destination:
        return "destination must be a non-empty string."
    ctx = invocation.ctx
    scratch_dir = ctx.session.scratch_dir
    resolved_source = _resolve(scratch_dir, Path(source))
    if not _within(resolved_source, scratch_dir):
        return f"{source!r} must resolve inside this Worker's scratch directory to be kept."
    resolved_destination = _resolve_destination(destination)
    if resolved_destination is None:
        return "destination must be an absolute path or a ~-rooted path, outside scratch."
    if _within(resolved_destination, scratch_dir):
        return f"{destination!r} resolves inside scratch; nothing needs keeping there."
    # Roadmap step 10.3a: the floors refuse a destination that is the Hive's own state.
    refused = await _destination_refusal(invocation, resolved_destination)
    if refused is not None:
        return refused
    try:
        content = await ctx.session.get_file(Path(source))
    except FileNotFoundError:
        return f"no file at {source!r}."
    except PathNotAllowedError as exc:
        raise UnreachablePathError(source) from exc
    request = _build_request(source, destination, content, resolved_destination)
    return describe(await cap(invocation, make_proposal(ctx, invocation.assignment, request)))


KEEP_SPEC = ToolSpec(definition=KEEP_DEFINITION, run=keep)


async def _destination_refusal(invocation: ToolInvocation, destination: Path) -> str | None:
    """Ask the Guard's floors alone about writing `destination`; the refusal line, or None."""
    target = Capability(family=CapabilityFamily.FS_WRITE, scope=destination.as_posix())
    return await floor_refusal_text(invocation, EnforcementPoint.SESSION_OUTSIDE_SCRATCH, target)


def _build_request(
    source: str, destination: str, content: bytes, resolved_destination: Path
) -> ProposalRequest:
    """Build the ProposalRequest for a COPY action, never carrying `content` itself on the wire."""
    action = ProposedAction(
        kind=ActionKind.COPY,
        summary=f"Keep {source} at {destination}"[:200],
        diff=None,
        diff_sha256=None,
        command=(),
        cwd=None,
        paths=(source, str(resolved_destination)),
        steps=(),
        copy_sha256=hashlib.sha256(content).hexdigest(),
        copy_size=len(content),
    )
    postconditions = (
        Postcondition(
            kind=PostconditionKind.FILE_EXISTS,
            subject=str(resolved_destination),
            argv=(),
            expected=None,
        ),
    )
    return ProposalRequest(
        tier=RiskTier.OUTSIDE_SCRATCH_WRITE,
        action=action,
        postconditions=postconditions,
        reason=f"Drone keep {source} -> {destination}",
    )


def _resolve_destination(destination: str) -> Path | None:
    """Expand and resolve `destination`, or None if it is not absolute (with or without `~`).

    A plain (non-async) helper, like every path-resolution helper in `hivemind.workers.tools.
    session`: `Path.expanduser`/`Path.resolve` are fast, in-memory-mostly calls, but
    flake8-async (ASYNC240) still flags a blocking pathlib call written directly inside an async
    function's body.
    """
    expanded = Path(destination).expanduser()
    if not expanded.is_absolute():
        return None
    return expanded.resolve(strict=False)


def _resolve(scratch_dir: Path, path: Path) -> Path:
    """Join `path` under `scratch_dir` when relative, then resolve it (collapsing ".." segments)."""
    joined = path if path.is_absolute() else scratch_dir / path
    return joined.resolve(strict=False)


def _within(resolved: Path, scratch_dir: Path) -> bool:
    """Return whether `resolved` equals `scratch_dir` or is somewhere underneath it."""
    scratch_resolved = scratch_dir.resolve(strict=False)
    return resolved == scratch_resolved or scratch_resolved in resolved.parents
