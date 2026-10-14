"""Build one Hive's RoyalJellyAccess: registry, Basket, deposit door, resident view and key.

The Comb Registry and the Basket store are opened with the Hive's other stores
(``hivemind.cli.stores.open_comb_registry`` and ``open_basket_store``); this module wires
everything that sits on top of them. The Basket signs every blob with the Hive's own Ed25519 key,
the same one the Virtual side signs its frames with (``load_or_mint_hive_signer`` over the secret
store at ``[hive] secrets_dir``), so one key vouches for the Hive everywhere and a verifier that
trusts it can check any manifest the Basket made. The deposit door gets the Hive's untrusted-content
scanner (text blobs are read before they are kept), the Honey Store's intake behind the Honey port
(a no-op that warns when there is no Honey Store), and the manifest's size cap. The Hive Stand's
own resident view of the Basket gets the ``[basket]`` default ceiling: a resident ceiling of 0
admits nothing (``decide_residency``), so it is never passed. ``[royal_jelly] sandbox =
"subprocess"`` is the development-only sandbox and logs a warning once at build. The SCAFFOLDER
slot is resolved like the Ripener: an unusable binding becomes None, logged once, and the Lab
simply cannot scaffold until it is fixed.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.cli.compose.royal_jelly``.
    Called by ``hivemind.cli.compose.hive.build_hive``. Calls into ``hivemind.basket``,
    ``hivemind.cell`` (identity, the Hive Stand's Cell id), ``hivemind.cli.compose.guard`` (the
    scanner), ``hivemind.common.secrets``, ``hivemind.guard.scanner``, ``hivemind.honey_store``,
    ``hivemind.llm``, ``hivemind.manifest`` and ``hivemind.royal_jelly``.

Key invariants:
    - Never raises for a missing Honey Store or an unusable SCAFFOLDER binding: it degrades and
      logs once with the reason.
    - The Basket signs with the same key ``_virtual_side`` loads: minted once into the secret
      store, then reused across restarts.
    - The resident ceiling passed to the Hive Stand's ResidentBasket is never 0.

See Also:
    - docs/adr/0047-quarantine-comb-gates-and-sandbox.md for the subprocess sandbox warning.
    - hivemind.cli.compose.honey for resolve_ripener, the pattern resolve_scaffolder follows.
    - hivemind.royal_jelly.access for RoyalJellyAccess, what this builds.
"""

from __future__ import annotations

import asyncio

from hivemind.basket import BasketDeposits, DepositWiring, HoneyRecordPort, ResidentBasket
from hivemind.cell import CellIdentity, hive_stand_cell_id
from hivemind.cell.local import host_platform
from hivemind.cli.compose.deps import HiveStores
from hivemind.cli.compose.guard import build_content_scanner
from hivemind.cli.compose.royal_jelly.honey_port import NectarHoneyPort, NullHoneyPort
from hivemind.common.errors import HiveMindError
from hivemind.common.logging import get_logger
from hivemind.common.secrets import FileSecretStore, load_or_mint_hive_signer
from hivemind.forage import ModelSlot
from hivemind.guard.scanner import ScanRecorder
from hivemind.honey_store.access import HoneyAccess
from hivemind.llm import BoundModel, ProviderRegistry
from hivemind.manifest import HiveManifest, RoyalJellySection, SandboxKind
from hivemind.royal_jelly.access import RoyalJellyAccess, limits_from_section
from waggle.clock import Clock
from waggle.ids import CellId
from waggle.messages.labels import OsFamily
from waggle.signing import Ed25519Signer, Ed25519Verifier

__all__ = ["build_royal_jelly", "resolve_scaffolder"]

log = get_logger(__name__)


def build_royal_jelly(
    manifest: HiveManifest,
    stores: HiveStores,
    registry: ProviderRegistry,
    honey: HoneyAccess | None,
    clock: Clock,
) -> RoyalJellyAccess | None:
    """Build the Royal Jelly Lab's and the Basket's handles over the Hive's opened stores.

    Args:
        manifest: The Hive's loaded manifest; ``[royal_jelly]``, ``[basket]`` and ``[hive]``.
        stores: The Hive's stores; the Comb Registry, the Basket store and the (veiled) trail.
        registry: Resolves the SCAFFOLDER slot.
        honey: The Hive's Honey Store handles; None writes no Honey record (and says so).
        clock: Injected time source for every id, stamp and event the handles mint.

    Returns:
        A RoyalJellyAccess; None only for a hand-built ``HiveStores`` without a Comb Registry or
        Basket store.
    """
    if stores.comb_registry is None or stores.basket is None:
        return None
    config, sandbox_os = _sandbox_platform(manifest.royal_jelly)
    signer = _hive_signer(manifest)
    signer_id = manifest.hive.node_id
    stand = hive_stand_cell_id(signer_id)
    return RoyalJellyAccess(
        registry=stores.comb_registry,
        basket=stores.basket,
        deposits=BasketDeposits(
            stores.basket,
            _honey_port(honey, stand),
            _wiring(manifest, stores, signer, clock),
            clock,
        ),
        resident=ResidentBasket(
            stores.basket, stand, manifest.basket.default_resident_ceiling_bytes, clock
        ),
        signer=signer,
        signer_id=signer_id,
        verifier=Ed25519Verifier({signer_id: signer.public_key_bytes}),
        config=config,
        basket_config=manifest.basket,
        limits=limits_from_section(config.limits),
        sandbox_kind=config.sandbox,
        scaffolder_bound=resolve_scaffolder(registry),
        sandbox_os=sandbox_os,
    )


def _sandbox_platform(config: RoyalJellySection) -> tuple[RoyalJellySection, OsFamily]:
    """Return the section and the sandbox's OS family; the subprocess sandbox is this host.

    ADR-0047's sandbox is an Ubuntu Virtual Cell, so ``[royal_jelly] sandbox_distribution``
    names the image's distribution and the family is Linux. The development subprocess sandbox
    runs on the Hive Stand itself, so the platform the path decision compares a target with is
    this host's own: its family, and its distribution label unless the operator wrote one.
    """
    if config.sandbox is not SandboxKind.SUBPROCESS:
        return config, OsFamily.LINUX
    # ADR-0047: the dev sandbox shares the Hive Stand's machine with no isolation to speak of.
    log.warning("royal_jelly.subprocess_sandbox", note="dev only; never for a real Hive")
    family, distribution = host_platform()
    if distribution is not None and "sandbox_distribution" not in config.model_fields_set:
        config = config.model_copy(update={"sandbox_distribution": distribution})
    return config, family


def _hive_signer(manifest: HiveManifest) -> Ed25519Signer:
    """Load the Hive's signing key from the secret store, minting it the first time.

    SAFETY: a fresh event loop for this one setup call, the same seam `_virtual_side` uses: the
    secret store is async, `build_hive` is a sync composition root, and the call is one small
    file read (or one write, the first time), never a network request.
    """
    secrets = FileSecretStore(manifest.resolve_path(manifest.hive.secrets_dir))
    return asyncio.run(load_or_mint_hive_signer(secrets))


def resolve_scaffolder(registry: ProviderRegistry) -> BoundModel | None:
    """Resolve the SCAFFOLDER slot, or None (logged once with why) when it cannot be bound.

    Args:
        registry: The Hive's provider registry.

    Returns:
        The bound scaffolder model; None when its binding is unknown, refused by ``[llm]
        offline`` or cannot be built from its configuration. The Lab then cannot scaffold.
    """
    try:
        return registry.bound(ModelSlot.SCAFFOLDER)
    except (HiveMindError, ValueError) as exc:
        # Degrade, never fail closed: the registry and the Basket work without a scaffolder.
        code = getattr(exc, "code", None)
        reason = f"{code}: {exc}" if isinstance(code, str) else f"{type(exc).__name__}: {exc}"
        log.warning("royal_jelly.scaffolder_unavailable", reason=reason)
        return None


def _wiring(
    manifest: HiveManifest, stores: HiveStores, signer: Ed25519Signer, clock: Clock
) -> DepositWiring:
    """Give the deposit door its scanner, a recorder on the trail, the Hive key and the size cap."""
    return DepositWiring(
        scanner=build_content_scanner(manifest),
        recorder=ScanRecorder(trail=stores.trail, identity=_identity(manifest), clock=clock),
        signer=signer,
        signer_id=manifest.hive.node_id,
        max_blob_bytes=manifest.basket.max_blob_bytes,
    )


def _honey_port(honey: HoneyAccess | None, stand: CellId) -> HoneyRecordPort:
    """Return the Nectar adapter over Honey's intake, or the warning no-op without a Honey Store."""
    if honey is None:
        return NullHoneyPort()
    return NectarHoneyPort(honey.intake, stand)


def _identity(manifest: HiveManifest) -> CellIdentity:
    """Return the ``system`` identity the Basket's scanner flags are recorded under."""
    return CellIdentity(hive_id=manifest.hive.id, node_id=manifest.hive.node_id, actor="system")
