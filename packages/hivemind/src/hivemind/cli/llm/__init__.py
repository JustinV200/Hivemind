"""Provide `hive llm`: inspect configured providers and slots, and send one test call.

A package since roadmap phases 6 and 7 each taught these commands one more model door (the
transcriber and the embedder): `commands` holds the typer group and its three commands
(`providers`, `slots`, `test`), `rows` builds the provider and slot rows they print, and `local`
holds the local-server management commands (`models`, `pull`, `keep-alive`, roadmap step 8.2).
This face exports `app`, the group `hivemind.cli.app` attaches as `hive llm`, and the provider
rows and model listing `hive doctor` (roadmap 14.2) reuses rather than probing a second way.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard). Called by `hivemind.cli.app` and
    `hivemind.cli.doctor`. Calls into this package's own modules only.

Key invariants:
    - No rule about what a provider or a slot binding *is* lives here; every one of those lives
      in `hivemind.llm` and `hivemind.forage` (codingrules section 2's CLI row).

See Also:
    - hivemind.cli.llm.commands for each command's own behaviour and invariants.
"""

from hivemind.cli.llm.commands import app
from hivemind.cli.llm.local import ModelListing
from hivemind.cli.llm.rows import ProviderRow, provider_rows

__all__ = ["ModelListing", "ProviderRow", "app", "provider_rows"]
