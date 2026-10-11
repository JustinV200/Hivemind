"""Re-export the ``[llm]`` schema: providers, slot bindings, grade floors and hosted servers.

The ``[llm]`` table outgrew one module under codingrules section 5.1's size limit and section
5.6's directory fan-out (roadmap step 8.3 added ``[llm.servers]`` to a schema directory already at
ten modules), so it is a package: ``providers`` (the ``[llm.providers]`` and ``[llm.slots]`` rows,
the capability overrides and the shared constants), ``section`` (``LlmSection`` and the validators
that tie the tables together), ``floors`` (``[llm.floors]``, roadmap step 8.1) and ``servers``
(``[llm.servers]``, roadmap step 8.3). A caller imports any of these names from here without
knowing which module defines it.

Fits into the Hive:
    Layer 1 (foundational services; capacity as data), inside ``hivemind.manifest.schema``.
    Imported by ``hivemind.manifest.schema`` (its own face) and ``hivemind.manifest.schema.
    manifest``. Calls into the four modules named above.

Key invariants:
    - Every public name the old single ``llm`` module offered is still importable from here.
    - This file holds re-exports and ``__all__`` only (codingrules section 5.4).

See Also:
    - .claude/codingrules.md section 5.2 for "a concept that needs a second file becomes a package".
    - hivemind.manifest.schema for the schema's own face.

Public API:
    - Providers (providers): ProviderSpec, ProviderKind, CapabilityOverrides, EmbeddingOptions,
      SlotBinding, ManifestKey, MANIFEST_KEY_PATTERN, IN_PROCESS_KINDS, EMBEDDING_ONLY_KINDS and
      the DEFAULT_* constants.
    - The section (section): LlmSection.
    - Floors (floors): FloorGrade, DEFAULT_FLOOR, DEFAULT_SLOT_FLOORS, complete_floors,
      reject_unknown_floor_keys.
    - Servers (servers): ServerSpec, check_servers, check_server_sources.
"""

from hivemind.manifest.schema.llm.floors import (
    DEFAULT_FLOOR,
    DEFAULT_SLOT_FLOORS,
    FloorGrade,
    complete_floors,
    reject_unknown_floor_keys,
)
from hivemind.manifest.schema.llm.providers import (
    API_KEY_ENV_PATTERN,
    DEFAULT_PROVIDER_SEATS,
    DEFAULT_PROVIDER_TIMEOUT_S,
    DEFAULT_REQUEST_TIMEOUT_S,
    EMBEDDING_ONLY_KINDS,
    IN_PROCESS_KINDS,
    MANIFEST_KEY_PATTERN,
    MAX_MANIFEST_KEY_CHARS,
    CapabilityOverrides,
    EmbeddingOptions,
    ManifestKey,
    ProviderKind,
    ProviderSpec,
    SlotBinding,
)
from hivemind.manifest.schema.llm.section import LlmSection
from hivemind.manifest.schema.llm.servers import ServerSpec, check_server_sources, check_servers

__all__ = [
    "API_KEY_ENV_PATTERN",
    "DEFAULT_FLOOR",
    "DEFAULT_PROVIDER_SEATS",
    "DEFAULT_PROVIDER_TIMEOUT_S",
    "DEFAULT_REQUEST_TIMEOUT_S",
    "DEFAULT_SLOT_FLOORS",
    "EMBEDDING_ONLY_KINDS",
    "IN_PROCESS_KINDS",
    "MANIFEST_KEY_PATTERN",
    "MAX_MANIFEST_KEY_CHARS",
    "CapabilityOverrides",
    "EmbeddingOptions",
    "FloorGrade",
    "LlmSection",
    "ManifestKey",
    "ProviderKind",
    "ProviderSpec",
    "ServerSpec",
    "SlotBinding",
    "check_server_sources",
    "check_servers",
    "complete_floors",
    "reject_unknown_floor_keys",
]
