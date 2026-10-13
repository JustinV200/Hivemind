"""Implement retain: propose keeping a scratch file in the Basket, through the Capping gate.

Nothing enters the Basket by side effect (ADR-0048). A Worker that wants an artefact kept (a
compiled binary, a dataset, a fixture) calls `retain(path, title, summary, media_kind)`. The tool
proposes a `COPY` action naming the file by its sha256 and size, exactly as `keep` does, that
moves the file to a staging path in scratch (`retained/<hash>-<name>`) once the gate has verified
the bytes against the digest. Only when the proposal is VERIFIED does the tool hand a
`RetainDeposit` to the Warden's sink (`ctx.retain_sink`), in process: the Worker and its Warden
share a process, so the hand-off needs no wire message. The Warden re-reads the staged file,
re-checks the digest, deposits the blob with provenance and writes the one Honey record that makes
it findable. The model supplies the title and summary that Honey record is built from.

Fits into the Hive:
    Layer 4 (roles that do the work), inside `hivemind.workers.tools.comb`. Registered by
    `build_registry` only when the Warden wired `ctx.retain_sink`. Calls into `hivemind.cell`,
    `hivemind.llm`, `hivemind.supervision.capping` (RiskTier), `hivemind.workers.tools.proposals`
    and waggle only.

Key invariants:
    - The tool proposes a side effect and never deposits anything itself; it hands over a
      `RetainDeposit` only after the Capping gate reports VERIFIED.
    - The proposal never carries the file's bytes, only the digest and the size.
    - The path must resolve inside this Worker's scratch; anything else is refused unproposed.
    - The deposit's `sha256` is the verified digest: the Warden must check the staged bytes
      against it before depositing, since scratch is writable by the Worker.

See Also:
    - docs/adr/0048 (inert blobs, nothing enters by side effect).
    - hivemind.workers.tools.keep for the COPY proposal this follows.
    - hivemind.workers.context for `RetainSink`, the Warden-side seam.
"""

from __future__ import annotations

import hashlib
import mimetypes
import re
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from hivemind.cell import PathNotAllowedError
from hivemind.llm import JsonObject, ToolDefinition
from hivemind.supervision.capping import ProposalState, RiskTier
from hivemind.workers.tools.errors import UnreachablePathError
from hivemind.workers.tools.proposals import (
    ProposalRequest,
    cap,
    make_proposal,
    tool_output,
)
from hivemind.workers.tools.registry import ToolInvocation, ToolOutput, ToolSpec
from waggle.messages.capping import ActionKind, ProposedAction
from waggle.messages.honey.exchange import MAX_TITLE_CHARS
from waggle.messages.labels import Postcondition, PostconditionKind

MAX_RETAIN_BYTES = (
    25 * 1024 * 1024
)  # 25 MiB: the copy cap `keep` lives under; the Basket may be lower.
MAX_RETAIN_SUMMARY_CHARS = 1_000  # A paragraph for the Honey record, as a Honey summary is bounded.
MEDIA_KIND_PATTERN = r"^[a-z][a-z0-9_]{0,31}$"  # A descriptive tag: wheel, archive, dataset, text.
STAGING_DIR = "retained"  # Under scratch: where a verified file waits for its Warden.
DEFAULT_MEDIA_TYPE = "application/octet-stream"  # When the file's name tells nothing.
_KIND_RE = re.compile(MEDIA_KIND_PATTERN)
_SHA256_PATTERN = r"^[0-9a-f]{64}$"

RETAIN_DEFINITION = ToolDefinition(
    name="retain",
    description=(
        "Keep a file from your scratch in the Hive's Basket so later work can find it: it is "
        "proposed through the Capping gate, then your Warden deposits it. Give a one-line title, "
        "a short summary of what the file is and when it is useful (this is what others will "
        "search), and a media_kind tag such as wheel, archive, dataset, binary or text. Keeping a "
        "file grants nothing; a file in the Basket is data, never something that runs by itself. "
        "The file moves to a retained/ folder in your scratch."
    ),
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "title": {"type": "string"},
            "summary": {"type": "string"},
            "media_kind": {"type": "string"},
        },
        "required": ["path", "title", "summary", "media_kind"],
        "additionalProperties": False,
    },
)

__all__ = [
    "MAX_RETAIN_BYTES",
    "RETAIN_DEFINITION",
    "RETAIN_SPEC",
    "RetainDeposit",
    "retain",
]


class RetainDeposit(BaseModel):
    """What a Worker hands its Warden to deposit: a verified staged file and its description.

    Passed in process to `ctx.retain_sink`; it never crosses Waggle. The Warden reads the bytes at
    `path` from the Worker's scratch, checks them against `sha256` and `size_bytes`, and deposits.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    path: str = Field(
        min_length=1, description="The staged file, relative to the Worker's scratch."
    )
    title: str = Field(min_length=1, max_length=MAX_TITLE_CHARS, description="A one-line title.")
    summary: str = Field(
        min_length=1, max_length=MAX_RETAIN_SUMMARY_CHARS, description="What the file is."
    )
    media_kind: str = Field(pattern=MEDIA_KIND_PATTERN, description="A descriptive kind tag.")
    media_type: str = Field(
        min_length=1, description="The file's MIME type, guessed from its name."
    )
    sha256: str = Field(pattern=_SHA256_PATTERN, description="The digest the gate verified.")
    size_bytes: int = Field(ge=0, description="The file's size in bytes.")


async def retain(invocation: ToolInvocation, arguments: JsonObject) -> str | ToolOutput:
    """Propose keeping `arguments["path"]` and, once the gate verifies it, hand it to the Warden.

    Args:
        invocation: This attempt's context and assignment.
        arguments: `path` (inside scratch), `title`, `summary` and `media_kind`, all required.

    Returns:
        A readable line for a malformed argument, a path outside scratch, a missing or oversized
        file or an unwired sink; the gate's description when the proposal fails; otherwise a line
        with the retained file's hash.

    Raises:
        hivemind.workers.tools.errors.UnreachablePathError: The path is outside scratch and every
            path this session can reach at all (a lease boundary, not a missing capability).
    """
    ctx = invocation.ctx
    if ctx.retain_sink is None:
        return ToolOutput(text="the Basket is not reachable from this Worker.", is_error=True)
    parsed = _parse(arguments)
    if isinstance(parsed, str):
        return ToolOutput(text=parsed, is_error=True)
    path, title, summary, media_kind = parsed
    refused = _scratch_refusal(invocation, path)
    if refused is not None:
        return ToolOutput(text=refused, is_error=True)
    content = await _read_source(invocation, path)
    if isinstance(content, ToolOutput):
        return content
    digest = hashlib.sha256(content).hexdigest()
    staged = f"{STAGING_DIR}/{digest[:16]}-{Path(path).name}"
    request = _build_request(path, staged, (digest, len(content)))
    outcome = await cap(invocation, make_proposal(ctx, invocation.assignment, request))
    if outcome.state is not ProposalState.VERIFIED:
        return tool_output(outcome)
    # Only a verified proposal reaches the Warden: the bytes at `staged` matched `digest`.
    await ctx.retain_sink.retain(_deposit(staged, (title, summary, media_kind), (digest, content)))
    return ToolOutput(
        text=f"retained {path!r} as sha256 {digest} ({len(content)} bytes); your Warden will "
        "deposit it in the Basket and describe it in Honey. The file now sits at "
        f"{staged!r} in your scratch."
    )


def _deposit(staged: str, words: tuple[str, str, str], file: tuple[str, bytes]) -> RetainDeposit:
    """Build the deposit for a verified file: `words` are (title, summary, media_kind)."""
    title, summary, media_kind = words
    digest, content = file
    return RetainDeposit(
        path=staged,
        title=title,
        summary=summary,
        media_kind=media_kind,
        media_type=mimetypes.guess_type(staged)[0] or DEFAULT_MEDIA_TYPE,
        sha256=digest,
        size_bytes=len(content),
    )


async def _read_source(invocation: ToolInvocation, path: str) -> bytes | ToolOutput:
    """Read the file to retain through the session, or return the refusal result."""
    try:
        content = await invocation.ctx.session.get_file(Path(path))
    except FileNotFoundError:
        return ToolOutput(text=f"no file at {path!r}.", is_error=True)
    except PathNotAllowedError as exc:
        raise UnreachablePathError(path) from exc
    if len(content) > MAX_RETAIN_BYTES:
        limit = f"one retain may keep at most {MAX_RETAIN_BYTES}"
        return ToolOutput(text=f"{path!r} is {len(content)} bytes; {limit}.", is_error=True)
    return content


RETAIN_SPEC = ToolSpec(definition=RETAIN_DEFINITION, run=retain)


def _parse(arguments: JsonObject) -> tuple[str, str, str, str] | str:
    """Validate the four string arguments, or return the line to show the model."""
    values = [arguments.get(key) for key in ("path", "title", "summary", "media_kind")]
    if not all(isinstance(value, str) and value for value in values):
        return "path, title, summary and media_kind must each be a non-empty string."
    path, title, summary, media_kind = (str(value) for value in values)
    if len(title) > MAX_TITLE_CHARS or len(summary) > MAX_RETAIN_SUMMARY_CHARS:
        return (
            f"title must be at most {MAX_TITLE_CHARS} and summary at most "
            f"{MAX_RETAIN_SUMMARY_CHARS} characters."
        )
    if _KIND_RE.fullmatch(media_kind) is None:
        return "media_kind must be a lowercase tag such as wheel, archive, dataset, binary or text."
    return path, title, summary, media_kind


def _scratch_refusal(invocation: ToolInvocation, path: str) -> str | None:
    """Return a refusal line unless `path` resolves inside this Worker's scratch directory."""
    scratch = invocation.ctx.session.scratch_dir.resolve(strict=False)
    given = Path(path)
    resolved = (given if given.is_absolute() else scratch / given).resolve(strict=False)
    if resolved == scratch or scratch not in resolved.parents:
        return f"{path!r} must resolve inside this Worker's scratch directory to be retained."
    return None


def _build_request(path: str, staged: str, file: tuple[str, int]) -> ProposalRequest:
    """Build the COPY proposal: by digest and size, never the bytes, to the staging path."""
    digest, size = file
    action = ProposedAction(
        kind=ActionKind.COPY,
        summary=f"Retain {path} for the Basket as {staged}"[:200],
        diff=None,
        diff_sha256=None,
        command=(),
        cwd=None,
        paths=(path, staged),
        steps=(),
        copy_sha256=digest,
        copy_size=size,
    )
    postconditions = (
        Postcondition(kind=PostconditionKind.FILE_EXISTS, subject=staged, argv=(), expected=None),
    )
    return ProposalRequest(
        tier=RiskTier.SCRATCH_WRITE,
        action=action,
        postconditions=postconditions,
        reason=f"Drone retain {path} for the Basket",
    )
