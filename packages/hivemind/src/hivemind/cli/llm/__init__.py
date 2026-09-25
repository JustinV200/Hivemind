"""Provide `hive llm`: inspect configured providers and slots, and send one test call.

A package since roadmap phases 6 and 7 each taught these commands one more model door (the
transcriber and the embedder): `commands` holds the typer group and its three commands
(`providers`, `slots`, `test`), `rows` builds the provider and slot rows they print. This face
exports only `app`, the group `hivemind.cli.app` attaches as `hive llm`.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard). Called by `hivemind.cli.app`. Calls into this
    package's own `commands` only.

Key invariants:
    - No rule about what a provider or a slot binding *is* lives here; every one of those lives
      in `hivemind.llm` and `hivemind.forage` (codingrules section 2's CLI row).

See Also:
    - hivemind.cli.llm.commands for each command's own behaviour and invariants.
"""

from hivemind.cli.llm.commands import app

__all__ = ["app"]
