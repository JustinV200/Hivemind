"""Bundle one Hive's Royal Jelly and Basket handles: the registry, the Basket and their policy.

The Royal Jelly Lab (the Hive's tool-authoring pipeline) and the Basket (its store of inert
blobs) need the same handful of collaborators together: the Comb Registry that keeps promoted
tools, the Basket store and the deposit door that fills it, the Hive Stand's own resident view of
it, the Hive key that signs every blob and a verifier that trusts it, the two manifest sections
that tune them, and the model bound to the SCAFFOLDER slot. The Queen's promotion flow, the Hive
Stand's Warden and the `hive tools` / `hive basket` commands all take the same bundle, so the
composition root builds it once and hands out this one frozen value instead of a dozen loose
collaborators (codingrules 5.1). ``limits_from_section`` turns the manifest's
``[royal_jelly.limits]`` into the Comb's own ``QuarantineLimits``; it lives here because the
manifest is a lower layer than the Lab and cannot import the model it mirrors.

Fits into the Hive:
    Layer 3 (sources of Cells, and capabilities handed down), inside ``hivemind.royal_jelly``.
    Built by the composition root (``hivemind.cli.compose.royal_jelly.build_royal_jelly``) and read
    by the Queen (``QueenDeps.royal_jelly``) and the Hive Stand's Warden
    (``WardenDeps.royal_jelly``). Calls into ``hivemind.basket``, ``hivemind.llm`` (the bound
    model type), ``hivemind.manifest`` (the two sections), this package's registry and spec
    modules and waggle's Ed25519 signing classes only.

Key invariants:
    - Every handle here shares one Basket store and one registry: whatever a consumer deposits or
      promotes lands in the same Hive database, signed with the same Hive key.
    - Holds no state of its own; every field is a collaborator, an immutable manifest section or
      an immutable value derived from one.
    - ``scaffolder_bound`` is None when the SCAFFOLDER slot cannot be resolved: the Lab then
      refuses to scaffold and says why, while the registry and the Basket work regardless.

See Also:
    - docs/adr/0046-tool-spec-comb-registry-and-scopes.md for the registry and its principals.
    - docs/adr/0048-basket-inert-blobs-residency-and-transfer.md for the Basket.
    - hivemind.cli.compose.royal_jelly for the one place a RoyalJellyAccess is built.
"""

from __future__ import annotations

from dataclasses import dataclass

from hivemind.basket import BasketDeposits, BasketStore, ResidentBasket
from hivemind.llm import BoundModel
from hivemind.manifest import BasketSection, QuarantineLimitsSection, RoyalJellySection, SandboxKind
from hivemind.royal_jelly.registry import CombRegistry
from hivemind.royal_jelly.spec import QuarantineLimits
from waggle.signing import Ed25519Signer, Ed25519Verifier

__all__ = ["RoyalJellyAccess", "limits_from_section"]


@dataclass(frozen=True, slots=True)
class RoyalJellyAccess:
    """One Hive's Royal Jelly Lab and Basket, ready to use: handles plus the policy they run under.

    Attributes:
        registry: The Comb Registry of promoted tools (the only writer of a PROMOTED row).
        basket: The Hive Stand's canonical Basket store.
        deposits: The one door a blob enters the Basket through (cap, label, scan, sign, describe).
        resident: The Hive Stand's own resident view of the Basket, under its disk ceiling.
        signer: The Hive key's signer; signs every blob manifest at deposit.
        signer_id: The node id a verifier looks the Hive key up under (``[hive] node_id``).
        verifier: Trusts exactly the Hive key; checks a manifest this Hive signed.
        config: ``[royal_jelly]``: the sandbox choice, its footprint and the retry budget.
        basket_config: ``[basket]``: where bytes live, size bounds and the shipping policy.
        limits: What one Quarantine Comb run may spend, from ``[royal_jelly.limits]``.
        sandbox_kind: Where the Comb runs (``config.sandbox``, repeated for a reader's ease).
        scaffolder_bound: The model on the SCAFFOLDER slot; None when no binding resolves.
    """

    registry: CombRegistry
    basket: BasketStore
    deposits: BasketDeposits
    resident: ResidentBasket
    signer: Ed25519Signer
    signer_id: str
    verifier: Ed25519Verifier
    config: RoyalJellySection
    basket_config: BasketSection
    limits: QuarantineLimits
    sandbox_kind: SandboxKind
    scaffolder_bound: BoundModel | None = None


def limits_from_section(section: QuarantineLimitsSection) -> QuarantineLimits:
    """Build the Comb's ``QuarantineLimits`` from the manifest's ``[royal_jelly.limits]``.

    Args:
        section: The validated manifest section.

    Returns:
        The same five limits as the Comb's own model (a test pins that the two stay equal).
    """
    # model_dump yields exactly the section's five fields, which are the model's five fields.
    return QuarantineLimits(**section.model_dump())
