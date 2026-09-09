"""Implement run_command, read_file and write_file: a Drone's built-in session and filesystem tools.

Every path a caller passes is relative to `ctx.session.scratch_dir` when it is relative
(codingrules section 8.7: "A session is a terminal"), matching every `CellSession` method's own
rule. `run_command` and `write_file` have a side effect, so both build a
`waggle.messages.capping.ProposedAction` and go through `hivemind.workers.tools.proposals.cap`
before anything runs or lands; `read_file` has none, so it goes straight to the session once a
capability check (only for a path outside scratch -- scratch is always readable) passes.

Fits into the Hive:
    Layer 4 (roles that do the work), inside `hivemind.workers.tools`. Registered by
    `hivemind.workers.tools.registry.build_registry`. Calls into `hivemind.cell`, `hivemind.guard`,
    `hivemind.llm` (ToolDefinition, JsonObject), `hivemind.supervision.capping` (RiskTier),
    `hivemind.workers.tools.errors`, `hivemind.workers.tools.proposals`,
    `hivemind.workers.tools.registry` and waggle only.

Key invariants:
    - `run_command` and `write_file` declare `RiskTier.SCRATCH_WRITE` when the resolved target is
      under scratch, `RiskTier.OUTSIDE_SCRATCH_WRITE` otherwise: nothing lands uncapped, even
      inside scratch, but the scratch-tier check ladder is cheap (codingrules section 8.12).
    - `write_file` builds a whole-file unified diff: a new-file hunk when the path has no prior
      content this session can read, a full-replacement hunk otherwise. It never computes a
      line-level patch.
    - `read_file` truncates its result to `MAX_TOOL_RESULT_CHARS`, with a marker, so one huge file
      never crowds out the rest of a Drone's hot state (mirrors codingrules section 8.9's item cap
      for hot-state packing, at the tool-result boundary instead).

See Also:
    - .claude/codingrules.md section 8.7 for "A session is a terminal."
    - .claude/codingrules.md section 8.12 for "Propose, then commit."
    - hivemind.workers.tools.proposals for make_proposal, cap and describe, this module's two
      side-effecting tools' one path to the Capping gate.
    - hivemind.cell.session for resolve_scratch_path, the stricter check a CellSession itself
      applies once a proposal actually reaches CAPPED.
"""

from __future__ import annotations

from pathlib import Path

from hivemind.cell import PathNotAllowedError
from hivemind.guard import Capability, CapabilityFamily
from hivemind.llm import JsonObject, ToolDefinition
from hivemind.supervision.capping import RiskTier
from hivemind.workers.context import WorkerContext
from hivemind.workers.tools.errors import UnreachablePathError
from hivemind.workers.tools.proposals import ProposalRequest, cap, describe, make_proposal
from hivemind.workers.tools.registry import ToolInvocation, ToolSpec
from waggle.messages.capping import ActionKind, ProposedAction
from waggle.messages.labels import Postcondition, PostconditionKind

MAX_TOOL_RESULT_CHARS = 8_000  # A generous read; beyond this the model gets a marker, not a wall.

RUN_COMMAND_DEFINITION = ToolDefinition(
    name="run_command",
    description=(
        "Run a command as an argument list (never a shell string) on this Worker's Cell, proposed "
        "through the Capping gate before it runs."
    ),
    parameters={
        "type": "object",
        "properties": {
            "argv": {"type": "array"},
            "cwd": {"type": "string"},
            "timeout_s": {"type": "number"},
        },
        "required": ["argv"],
        "additionalProperties": False,
    },
)
READ_FILE_DEFINITION = ToolDefinition(
    name="read_file",
    description="Read a text file from this Worker's Cell; scratch is always readable.",
    parameters={
        "type": "object",
        "properties": {"path": {"type": "string"}},
        "required": ["path"],
        "additionalProperties": False,
    },
)
WRITE_FILE_DEFINITION = ToolDefinition(
    name="write_file",
    description=(
        "Write a text file on this Worker's Cell, proposed through the Capping gate before it "
        "lands."
    ),
    parameters={
        "type": "object",
        "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
        "required": ["path", "content"],
        "additionalProperties": False,
    },
)

__all__ = [
    "MAX_TOOL_RESULT_CHARS",
    "READ_FILE_DEFINITION",
    "READ_FILE_SPEC",
    "RUN_COMMAND_DEFINITION",
    "RUN_COMMAND_SPEC",
    "WRITE_FILE_DEFINITION",
    "WRITE_FILE_SPEC",
    "read_file",
    "run_command",
    "write_file",
]


async def run_command(invocation: ToolInvocation, arguments: JsonObject) -> str:
    """Propose running `arguments["argv"]` and report the Capping gate's verdict.

    Args:
        invocation: This attempt's context and assignment.
        arguments: `argv` (required, a non-empty list of strings), `cwd` (optional, relative to
            scratch when relative) and `timeout_s` (accepted for schema compatibility; v0's
            `apply_action` always uses its own fixed command timeout, so this has no effect yet).

    Returns:
        A readable string for a malformed `argv`; otherwise `hivemind.workers.tools.proposals.
        describe`'s rendering of the gate's outcome. The gate exposes no captured stdout/stderr
        and, on success, no exit code (only a rolled-back COMMAND's own failure reason names one)
        -- this result carries state and reason only, never more than the gate itself surfaces.
    """
    argv = _coerce_argv(arguments.get("argv"))
    if not argv:
        return "argv must be a non-empty list of strings."
    ctx = invocation.ctx
    cwd = arguments.get("cwd")
    cwd_str = cwd if isinstance(cwd, str) else None
    resolved_cwd = (
        ctx.session.scratch_dir.resolve(strict=False)
        if cwd_str is None
        else _resolve(ctx.session.scratch_dir, Path(cwd_str))
    )
    tier = (
        RiskTier.SCRATCH_WRITE
        if _within_scratch(resolved_cwd, ctx.session.scratch_dir)
        else RiskTier.OUTSIDE_SCRATCH_WRITE
    )
    action = ProposedAction(
        kind=ActionKind.COMMAND,
        summary=f"Run {' '.join(argv)[:200]}",
        diff=None,
        diff_sha256=None,
        command=argv,
        cwd=cwd_str,
        paths=(),
        steps=(),
    )
    request = ProposalRequest(
        tier=tier, action=action, postconditions=(), reason=f"Drone run_command {argv[0]!r}"
    )
    proposal = make_proposal(ctx, invocation.assignment, request)
    return describe(await cap(ctx, proposal))


async def read_file(invocation: ToolInvocation, arguments: JsonObject) -> str:
    """Read `arguments["path"]` and return its text, truncated to `MAX_TOOL_RESULT_CHARS`.

    Args:
        invocation: This attempt's context and assignment.
        arguments: `path` (required, relative to scratch when relative).

    Returns:
        The file's text (utf-8, decode errors replaced), truncated with a marker when long; a
        readable string when the path is malformed, uncovered by any `fs:read` capability, or
        does not exist.

    Raises:
        hivemind.workers.tools.errors.UnreachablePathError: `path` is outside scratch and outside
            every path this Worker's session can reach at all (a session/lease boundary, not a
            missing capability).
    """
    path = arguments.get("path")
    if not isinstance(path, str) or not path:
        return "path must be a non-empty string."
    ctx = invocation.ctx
    resolved = _resolve(ctx.session.scratch_dir, Path(path))
    if not _within_scratch(resolved, ctx.session.scratch_dir):
        needed = Capability(family=CapabilityFamily.FS_READ, scope=resolved.as_posix())
        if not ctx.capabilities.allows(needed):
            return f"no fs:read capability covers {resolved.as_posix()}."
    try:
        data = await ctx.session.get_file(Path(path))
    except FileNotFoundError:
        return f"no file at {path!r}."
    except PathNotAllowedError as exc:
        raise UnreachablePathError(path) from exc
    return _truncate(data.decode("utf-8", errors="replace"))


async def write_file(invocation: ToolInvocation, arguments: JsonObject) -> str:
    """Propose writing `arguments["content"]` to `arguments["path"]` as a whole-file diff.

    Args:
        invocation: This attempt's context and assignment.
        arguments: `path` (required, relative to scratch when relative) and `content` (required).

    Returns:
        A readable string for malformed arguments; otherwise `hivemind.workers.tools.proposals.
        describe`'s rendering of the gate's outcome.
    """
    path = arguments.get("path")
    content = arguments.get("content")
    if not isinstance(path, str) or not path:
        return "path must be a non-empty string."
    if not isinstance(content, str):
        return "content must be a string."
    ctx = invocation.ctx
    prior = await _read_prior(ctx, path)
    resolved = _resolve(ctx.session.scratch_dir, Path(path))
    tier = (
        RiskTier.SCRATCH_WRITE
        if _within_scratch(resolved, ctx.session.scratch_dir)
        else RiskTier.OUTSIDE_SCRATCH_WRITE
    )
    action = ProposedAction(
        kind=ActionKind.DIFF,
        summary=f"Write {path}"[:200],
        diff=_build_whole_file_diff(prior, content),
        diff_sha256=None,
        command=(),
        cwd=None,
        paths=(path,),
        steps=(),
    )
    postconditions = (
        Postcondition(kind=PostconditionKind.FILE_EXISTS, subject=path, argv=(), expected=None),
    )
    request = ProposalRequest(
        tier=tier, action=action, postconditions=postconditions, reason=f"Drone write_file {path}"
    )
    proposal = make_proposal(ctx, invocation.assignment, request)
    return describe(await cap(ctx, proposal))


RUN_COMMAND_SPEC = ToolSpec(definition=RUN_COMMAND_DEFINITION, run=run_command)
READ_FILE_SPEC = ToolSpec(definition=READ_FILE_DEFINITION, run=read_file)
WRITE_FILE_SPEC = ToolSpec(definition=WRITE_FILE_DEFINITION, run=write_file)


def _coerce_argv(value: object) -> tuple[str, ...]:
    """Return `value` as a tuple of strings, or an empty tuple when it is not one.

    A schema's `type: array` only proves `value` is a list (`hivemind.llm.ladders.extraction`'s
    documented subset does not check element types); this is the one-level-deeper check that
    subset leaves to the tool itself.
    """
    if not isinstance(value, list) or not value:
        return ()
    if not all(isinstance(item, str) for item in value):
        return ()
    return tuple(value)


async def _read_prior(ctx: WorkerContext, path: str) -> bytes | None:
    """Read `path`'s current bytes through the session, or None when unreadable or absent.

    A `PathNotAllowedError` here (a path this session cannot reach at all) is treated the same as
    `FileNotFoundError`: `write_file` still builds a best-effort new-file diff and submits it as a
    Proposal, letting the Capping gate's own checks (which read reachability from the same lease)
    make the real accept/reject decision, rather than this best-effort read raising on its own.
    """
    try:
        return await ctx.session.get_file(Path(path))
    except (FileNotFoundError, PathNotAllowedError):
        return None


def _build_whole_file_diff(prior: bytes | None, content: str) -> str:
    """Build a whole-file unified diff: a new-file hunk, or a full-replacement hunk.

    Args:
        prior: The path's current bytes, or None for a new file.
        content: The full text the path should hold afterwards.

    Returns:
        A unified diff `hivemind.supervision.capping.diff.apply_unified_diff` accepts.
    """
    new_lines = content.splitlines()
    if prior is None:
        added = "\n".join(f"+{line}" for line in new_lines)
        header = f"@@ -0,0 +1,{len(new_lines)} @@\n"
        return f"{header}{added}\n" if added else header
    prior_lines = prior.decode("utf-8", errors="replace").splitlines()
    removed = "\n".join(f"-{line}" for line in prior_lines)
    added = "\n".join(f"+{line}" for line in new_lines)
    body = "\n".join(part for part in (removed, added) if part)
    header = f"@@ -1,{len(prior_lines)} +1,{len(new_lines)} @@\n"
    return f"{header}{body}\n" if body else header


def _resolve(scratch_dir: Path, path: Path) -> Path:
    """Join `path` under `scratch_dir` when relative, then resolve it (collapsing ".." segments)."""
    joined = path if path.is_absolute() else scratch_dir / path
    return joined.resolve(strict=False)


def _within_scratch(resolved: Path, scratch_dir: Path) -> bool:
    """Return whether `resolved` equals `scratch_dir` or is somewhere underneath it."""
    scratch_resolved = scratch_dir.resolve(strict=False)
    return resolved == scratch_resolved or scratch_resolved in resolved.parents


def _truncate(text: str) -> str:
    """Cap `text` to `MAX_TOOL_RESULT_CHARS`, with a marker naming the true length when cut."""
    if len(text) <= MAX_TOOL_RESULT_CHARS:
        return text
    return f"{text[:MAX_TOOL_RESULT_CHARS]}...[truncated, {len(text)} chars total]"
