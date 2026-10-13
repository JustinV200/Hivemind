"""Offer the Hive's promoted tools to a Worker, and let it ask for new ones and keep artefacts.

Phase 9's Worker half (roadmap 9.7). A Worker never reads the Comb Registry (the catalogue of
promoted tools): its Warden hands it a bundle of `CombToolEntry` values, `build_registry` offers
each as a tool, and `CombToolRunner` re-validates every call and loads the tool's package only
after its SHA-256 matches the entry. `request_tool` asks the Hive to author a missing tool;
`retain` proposes keeping a scratch file in the Basket. Split into a package because the three
concerns (load and run, request, retain) are each a module of their own and `hivemind.workers.
tools` already holds ten.

Fits into the Hive:
    Layer 4 (roles that do the work), inside `hivemind.workers.tools`. Registered by
    `build_registry`; its bundle is filled by the Warden (wave 3H), its channel by
    `hivemind.workers.runtime.tools`. Calls into `hivemind.royal_jelly` (rows, hashes, events),
    `hivemind.guard`, `hivemind.llm`, `hivemind.supervision.capping` and waggle.

Key invariants:
    - A hash with no `CombToolEntry` cannot be invoked: nothing here takes a bare hash.
    - The bundle is the one mutable thing a Warden may change under a Worker.

See Also:
    - docs/adr/0046 and docs/adr/0048 for the rules, and .claude/roadmap.md step 9.7.
    - tests/unit/workers/tools/test_invoke_inert.py for the proof of the first invariant.

Public API:
    - CombToolEntry, PackageSource, CombToolBundle (entry): what a Worker is handed.
    - comb_tool_spec, comb_specs, CombToolRunner (invoke): offering and running an entry.
    - PackageLoader, PathImporter, ToolLoadError (loader): unpack-and-import with a cache.
    - request_tool, REQUEST_TOOL_SPEC (request): ask the Hive for a new tool.
    - retain, RETAIN_SPEC, RetainDeposit (retain): propose keeping a scratch file.
"""

from hivemind.workers.tools.comb.entry import CombToolBundle, CombToolEntry, PackageSource
from hivemind.workers.tools.comb.invoke import (
    COMB_TOOL_TIMEOUT_S,
    MAX_COMB_RESULT_CHARS,
    CombToolRunner,
    comb_specs,
    comb_tool_spec,
)
from hivemind.workers.tools.comb.loader import (
    ModuleImporter,
    PackageLoader,
    PathImporter,
    ToolLoadError,
)
from hivemind.workers.tools.comb.request import (
    REQUEST_TOOL_DEFINITION,
    REQUEST_TOOL_SPEC,
    request_tool,
)
from hivemind.workers.tools.comb.retain import (
    MAX_RETAIN_BYTES,
    RETAIN_DEFINITION,
    RETAIN_SPEC,
    RetainDeposit,
    retain,
)

__all__ = [
    "COMB_TOOL_TIMEOUT_S",
    "MAX_COMB_RESULT_CHARS",
    "MAX_RETAIN_BYTES",
    "REQUEST_TOOL_DEFINITION",
    "REQUEST_TOOL_SPEC",
    "RETAIN_DEFINITION",
    "RETAIN_SPEC",
    "CombToolBundle",
    "CombToolEntry",
    "CombToolRunner",
    "ModuleImporter",
    "PackageLoader",
    "PackageSource",
    "PathImporter",
    "RetainDeposit",
    "ToolLoadError",
    "comb_specs",
    "comb_tool_spec",
    "request_tool",
    "retain",
]
