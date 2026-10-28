"""Render the starter Hive Manifest `hive init` writes, one template per provider path.

Roadmap 14.2. A newcomer picks a provider path (hosted Anthropic, Ollama, or any other
OpenAI-compatible server) and gets a manifest that validates, binds every model slot and enables
the Hive Stand, so their first `hive run` needs no Docker. The templates are package data beside
`render.py`; model ids and base URLs live only there and in that module (codingrules 8.6).

Fits into the Hive:
    Layer 1 (foundational services; configuration), inside `hivemind.manifest`. Called by
    `hivemind.cli.init`. Calls into `hivemind.manifest.schema` and `waggle` only.

Key invariants:
    - This file holds re-exports and `__all__` only.

See Also:
    - docs/manifests/README.md for the hand-written example manifests.

Public API:
    - render_starter, StarterChoice, StarterProvider, OLLAMA_BASE_URL (render): the starter text
      for one provider path, validated.
"""

from hivemind.manifest.starter.render import (
    OLLAMA_BASE_URL,
    StarterChoice,
    StarterProvider,
    render_starter,
)

__all__ = ["OLLAMA_BASE_URL", "StarterChoice", "StarterProvider", "render_starter"]
