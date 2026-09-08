"""Re-export the tool family: a tool asked for, promoted, invoked and its result returned.

Waggle is the Hive's bee-to-bee wire protocol (named after the honeybee waggle dance). A tool is a
versioned capability that the Royal Jelly Lab promotes and a Worker (the bee that does the work)
runs on a Cell. ``authoring`` holds the request for a tool and the announcement of its promotion;
``call`` the invocation of one version and its result. The JSON-text checks both share live in
``json_text`` and are not part of the face: they are validators, not wire values. This package is
the family's face: a caller imports any of its messages, enums or value models from here without
knowing which module defines them. The bounds each module names stay in that module, because the
spec makes the number normative, not the name.

Fits into the Hive:
    Its own layer (used by every layer in hivemind and by pollen, the lightweight device
    connector), inside the waggle package. Imported by waggle.messages (the catalogue and
    the package face) and by every bee that builds or reads a tool payload; calls into
    nothing beyond its own modules.

Key invariants:
    - Every class the registry registers under ``tool.*`` is re-exported here
      (tests/messages/test_registry_catalogue.py checks it).
    - This file holds re-exports and __all__ only; no message, enum or bound is defined here.

See Also:
    - docs/waggle/spec.md section 8.8 for the family's normative fields and rules.
    - waggle.messages.tool.authoring, waggle.messages.tool.call and
      waggle.messages.tool.json_text for the definitions.

Public API:
    - Authoring (authoring): QuarantinePath, ToolPromoted, ToolRequest, ToolScope.
    - Call (call): ToolInvoke, ToolInvokePurpose, ToolOutcome, ToolResult.
"""

from waggle.messages.tool.authoring import QuarantinePath, ToolPromoted, ToolRequest, ToolScope
from waggle.messages.tool.call import ToolInvoke, ToolInvokePurpose, ToolOutcome, ToolResult

__all__ = [
    "QuarantinePath",
    "ToolInvoke",
    "ToolInvokePurpose",
    "ToolOutcome",
    "ToolPromoted",
    "ToolRequest",
    "ToolResult",
    "ToolScope",
]
