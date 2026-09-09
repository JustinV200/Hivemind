"""Hold the tool implementations a Worker calls while it works: the tools package.

Roadmap step 3.16 populates this package for the Drone, the first role to call built-in tools:
`registry` fixes the seam every tool implements (`ToolSpec`, `ToolInvocation`, `ToolRunner`) and
the one place they are offered and run (`ToolRegistry`, `build_registry`); `session` is
`run_command`, `read_file` and `write_file`, each going through its Cell's `CellSession` rather
than touching a process or file directly; `http` is `http_request`, gated on a `net` capability and
always refused by v0's Capping gate (`waggle.messages.capping.ActionKind` has no network shape
yet); `ask` raises a blocking `Question` up the chain; `proposals` is the one place a tool's side
effect turns into a Capping `Proposal` and a `GateOutcome` turns back into tool-result text; and
`errors` is this package's own error tree, rooted at `hivemind.workers.errors.WorkerError`.

Fits into the Hive:
    Layer 4 (roles that do the work), inside the workers package. Called by
    `hivemind.workers.roles.drone.Drone` (roadmap step 3.16); calls into `hivemind.cell`,
    `hivemind.guard`, `hivemind.llm`, `hivemind.supervision.capping`, `hivemind.workers.context`,
    `hivemind.workers.errors`, waggle and, inside `http._send` only, `httpx`.

Key invariants:
    - Every tool call, whichever rung of a degradation ladder produced it, is validated against
      its own JSON schema by `ToolRegistry.execute` before it runs (codingrules section 15).
    - Nothing outside `http.py`'s `_send` opens a socket, and v0's Capping gate never reaches the
      state that would call it (`hivemind.supervision.capping.checks.deterministic.SchemaCheck`
      rejects every `ACTION_SEQUENCE` action).
    - Every tool with a side effect (`run_command`, `write_file`, `http_request`) proposes through
      `hivemind.workers.tools.proposals.cap` before anything runs or lands; `read_file` and `ask`
      have none and go straight to their collaborator.

See Also:
    - .claude/codingrules.md section 3 for where this sub-package sits under workers.
    - .claude/codingrules.md section 8.12 for "Propose, then commit," the shape every
      side-effecting tool here follows.
    - .claude/codingrules.md section 15 for the schema-and-capability validation every tool call
      passes through.
    - .claude/roadmap.md phase 3 step 3.16 for the work that populates this package.
    - hivemind.workers.tools.README for the module-by-module map of this package.

Public API (roadmap 3.16):
    - ToolInvocation, ToolRegistry, ToolRunner, ToolSpec, build_registry: the registry seam
      (hivemind.workers.tools.registry).
    - run_command, read_file, write_file, RUN_COMMAND_DEFINITION, READ_FILE_DEFINITION,
      WRITE_FILE_DEFINITION, RUN_COMMAND_SPEC, READ_FILE_SPEC, WRITE_FILE_SPEC,
      MAX_TOOL_RESULT_CHARS: the session/filesystem tools (hivemind.workers.tools.session).
    - http_request, HTTP_DEFINITION, HTTP_METHODS, HTTP_SPEC: the network tool
      (hivemind.workers.tools.http).
    - ask, ASK_DEFINITION, ASK_SPEC: the blocking-question tool (hivemind.workers.tools.ask).
    - ProposalRequest, make_proposal, cap, describe: a Proposal in, a GateOutcome out
      (hivemind.workers.tools.proposals).
    - ToolError, UnreachablePathError: this package's own error tree
      (hivemind.workers.tools.errors).
"""

from hivemind.workers.tools.ask import ASK_DEFINITION, ASK_SPEC, ask
from hivemind.workers.tools.errors import ToolError, UnreachablePathError
from hivemind.workers.tools.http import HTTP_DEFINITION, HTTP_METHODS, HTTP_SPEC, http_request
from hivemind.workers.tools.proposals import ProposalRequest, cap, describe, make_proposal
from hivemind.workers.tools.registry import (
    ToolInvocation,
    ToolRegistry,
    ToolRunner,
    ToolSpec,
    build_registry,
)
from hivemind.workers.tools.session import (
    MAX_TOOL_RESULT_CHARS,
    READ_FILE_DEFINITION,
    READ_FILE_SPEC,
    RUN_COMMAND_DEFINITION,
    RUN_COMMAND_SPEC,
    WRITE_FILE_DEFINITION,
    WRITE_FILE_SPEC,
    read_file,
    run_command,
    write_file,
)

__all__ = [
    "ASK_DEFINITION",
    "ASK_SPEC",
    "HTTP_DEFINITION",
    "HTTP_METHODS",
    "HTTP_SPEC",
    "MAX_TOOL_RESULT_CHARS",
    "READ_FILE_DEFINITION",
    "READ_FILE_SPEC",
    "RUN_COMMAND_DEFINITION",
    "RUN_COMMAND_SPEC",
    "WRITE_FILE_DEFINITION",
    "WRITE_FILE_SPEC",
    "ProposalRequest",
    "ToolError",
    "ToolInvocation",
    "ToolRegistry",
    "ToolRunner",
    "ToolSpec",
    "UnreachablePathError",
    "ask",
    "build_registry",
    "cap",
    "describe",
    "http_request",
    "make_proposal",
    "read_file",
    "run_command",
    "write_file",
]
