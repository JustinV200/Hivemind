"""Re-export the honey family: Nectar deposited into the Honey Store and Honey queried out of it.

Waggle is the Hive's bee-to-bee wire protocol (named after the honeybee waggle dance). Honey is the
Hive's distilled knowledge and Nectar the raw material it is made from; the Honey Store is the cold
tier that ripens one into the other. ``exchange`` holds the traffic with the store (a Nectar
deposit in, a Honey query and its response out); ``hit`` the retrieval result one response carries
and the provenance behind it. This package is the family's face: a caller imports any of its
messages, enums or value models from here without knowing which module defines them. The bounds
each module names stay in that module, because the spec makes the number normative, not the name.

Fits into the Hive:
    Its own layer (used by every layer in hivemind and by pollen, the lightweight device
    connector), inside the waggle package. Imported by waggle.messages (the catalogue and
    the package face) and by every bee that builds or reads a honey payload; calls into
    nothing beyond its own modules.

Key invariants:
    - Every class the registry registers under ``honey.*`` is re-exported here
      (tests/messages/test_registry_catalogue.py checks it).
    - This file holds re-exports and __all__ only; no message, enum or bound is defined here.

See Also:
    - docs/waggle/spec.md section 8.7 for the family's normative fields and rules.
    - waggle.messages.honey.exchange and waggle.messages.honey.hit for the definitions.

Public API:
    - Exchange (exchange): HoneyQuery, HoneyResponse, NectarDeposit, NectarKind.
    - Hit (hit): HoneyHit, HoneyProvenance.
"""

from waggle.messages.honey.exchange import HoneyQuery, HoneyResponse, NectarDeposit, NectarKind
from waggle.messages.honey.hit import HoneyHit, HoneyProvenance

__all__ = [
    "HoneyHit",
    "HoneyProvenance",
    "HoneyQuery",
    "HoneyResponse",
    "NectarDeposit",
    "NectarKind",
]
