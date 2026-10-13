"""Build the Royal Jelly Lab's and the Basket's handles from a Hive Manifest, once per Hive.

``build_royal_jelly`` is the composition root for phase 9: it takes the stores the Hive already
opened (the Comb Registry and the Basket store), signs with the Hive's own key, plugs the Honey
Store's intake in behind the Basket's Honey port, wires the Hive Stand's resident view of the
Basket and resolves the SCAFFOLDER slot, returning one ``RoyalJellyAccess`` the Queen and the
Hive Stand's Warden both hold. It is a package, not a module, because the Honey port adapter is a
second concept (codingrules 5.2) and ``hivemind.cli.compose`` already holds ten modules
(codingrules 5.6).

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside the CLI's composition root. Called by
    ``hivemind.cli.compose.hive.build_hive``. Calls into ``hivemind.basket``,
    ``hivemind.honey_store``, ``hivemind.royal_jelly``, ``hivemind.llm``, ``hivemind.manifest``
    and ``hivemind.common.secrets``.

Key invariants:
    - The registry and the Basket have no optional dependency, so a Hive whose stores carry both
      always gets an access; only a hand-built ``HiveStores`` without them gets None.
    - A missing Honey Store or SCAFFOLDER binding degrades (a recorder that warns, no scaffolder),
      it never fails the build.

See Also:
    - docs/adr/0046-tool-spec-comb-registry-and-scopes.md and docs/adr/0048-basket-inert-blobs-
      residency-and-transfer.md for what this wires.
    - hivemind.royal_jelly.access for RoyalJellyAccess, what this builds.

Public API:
    - build_royal_jelly, resolve_scaffolder (build): the composition entry points.
    - NectarHoneyPort, NullHoneyPort (honey_port): the Basket's Honey port adapters.
"""

from hivemind.cli.compose.royal_jelly.build import build_royal_jelly, resolve_scaffolder
from hivemind.cli.compose.royal_jelly.honey_port import NectarHoneyPort, NullHoneyPort

__all__ = ["NectarHoneyPort", "NullHoneyPort", "build_royal_jelly", "resolve_scaffolder"]
