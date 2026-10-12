"""Generate a candidate tool from a request: the scaffolder, its values and its fake.

The Royal Jelly Lab (the Hive's tool-authoring pipeline) starts with a scaffold: a model on the
SCAFFOLDER slot writes a tool's tests, source and declarations from a bee's brief, and the Hive
validates the spec, proves it asks for no more than its requester holds, and packs the three
files into a package named by its SHA-256. The result is a candidate, not a tool: it runs nowhere
until the Quarantine Comb (the sandbox every new tool must pass) has proved it.

Fits into the Hive:
    Layer 3 (sources of Cells, and capabilities handed down), inside the royal_jelly package.
    Called by the promotion flow (`hivemind.royal_jelly.promotion`); calls into `hivemind.llm`
    (the structured ladder and the shipped prompt), `hivemind.guard` (capability sets) and the
    spec package. Never imports the Quarantine Comb or the registry.

Key invariants:
    - A model's output is untrusted: it is validated, bounded and packed here, and executed only
      inside the Quarantine Comb.
    - The spec's identity and platform come from the brief, never from the model.

See Also:
    - docs/adr/0046 (tool spec, Comb Registry and scopes) for the spec rules.
    - .claude/roadmap.md step 9.3 for the scaffolder's requirements.
    - hivemind.royal_jelly.promotion for the flow that calls it.

Public API:
    - ToolRequestBrief, ScaffoldReply, ScaffoldResult (models): the brief, the model's reply and
      the validated, packaged outcome.
    - Scaffolder (protocol), ModelScaffolder (scaffolder): the seam and its model-backed version.
    - FakeScaffolder (fake): the scripted double for tests.
    - ScaffoldFailedError (errors): no usable reply could be produced.
"""

from hivemind.royal_jelly.scaffold.errors import ScaffoldFailedError
from hivemind.royal_jelly.scaffold.fake import FakeScaffolder, ScaffoldScript
from hivemind.royal_jelly.scaffold.models import ScaffoldReply, ScaffoldResult, ToolRequestBrief
from hivemind.royal_jelly.scaffold.scaffolder import ModelScaffolder, Scaffolder, assemble

__all__ = [
    "FakeScaffolder",
    "ModelScaffolder",
    "ScaffoldFailedError",
    "ScaffoldReply",
    "ScaffoldResult",
    "ScaffoldScript",
    "Scaffolder",
    "ToolRequestBrief",
    "assemble",
]
