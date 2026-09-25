"""Define the backend-independent seam every CellBackend uses to bring a fresh Cell up.

Provisioning a Virtual Cell (a VM or container the Hive creates and later destroys) always needs
the same three things, whatever the infrastructure underneath: a fresh identity for the Cell to
boot as (`CellBootstrap`), the Queen's own address as reachable from inside that Cell
(`QueenEndpoint`), and a way to learn when the Cell has actually announced itself
(`ReadinessGate`). `hivemind.hive.backends.docker` (roadmap step 5.4) is the first backend to use
this module; the QEMU and cloud backends (roadmap steps 5.11, 5.12) reuse it unchanged, which is
why it lives apart from `docker.py` instead of inside it. ADR-0027 sets the shape this module
implements: a Virtual Cell exposes no inbound port, so its `images/base-ubuntu` entry point dials
**out** to the Queen, reading its own identity from the `HIVEMIND_*` environment variables
`CellBootstrap.environment()` renders (that image's own README documents the exact variable list
this module's constants mirror), and the Queen must know the Cell's public key -- minted here,
before the container exists -- to verify the signed frames that Cell sends once connected.

A Night Veil Cell reaches the Queen another way (codingrules 8.7: its Waggle link goes over Tor
to a `.onion` hidden service, never the VPN tunnel or a clearnet address), so a `QueenEndpoint`
may carry a `NightVeilLink` beside its ordinary URL, and `cell_endpoint` is the one choice every
backend makes when it mints a Cell: the ordinary endpoint for MEADOW and PROPOLIS, the Night Veil
link (its hidden-service URL and the Tor SOCKS proxy on the Cell's own loopback) for NIGHT_VEIL,
and a refusal (`CellProvisionError`) for a Night Veil Cell whose Hive configured no link, so such
a Cell is never handed a clearnet address (roadmap step 10.3a).

`ReadinessGate` is deliberately only a Protocol: the real, Queen-side implementation (matching a
freshly-dialled Cell's `node_id`, chosen at random inside the container at boot per
`hivemind.cli.in_cell.config`, back to the `cell_id` this module minted for it) is a later
roadmap step's job. This module only defines the seam a `CellBackend` provisions against, plus
`FakeReadinessGate` (`hivemind.hive.backends.fake`) so `DockerCellBackend` and its tests need no
real Queen to run against.

Fits into the Hive:
    Layer 3 (sources of Cells). Called by `hivemind.hive.backends.docker` (roadmap step 5.4) and,
    later, `hivemind.hive.backends.qemu` and `hivemind.hive.backends.cloud`. Calls into
    hivemind.cell (CellCapabilities), hivemind.forage (ForageCapacity), waggle.clock, waggle.ids
    and waggle.signing only.

Key invariants:
    - `CellBootstrap.private_key_hex` is a pydantic `SecretStr`: its own `repr` redacts the value,
      so the default dataclass repr (and any log line, per codingrules 13/15) never shows key
      material; only `environment()` calls `get_secret_value()`, and only to hand the key to the
      Cell that owns it.
    - `mint_cell_bootstrap` mints a fresh Ed25519 keypair on every call, and a fresh `CellId`
      unless `cell_endpoint` carried the one the Cell's lifecycle already minted for it (so the
      Cell's first record, made before any backend is called, names the Cell that boots): keys
      are per Cell, never reused (ADR-0027: "each Cell gets its own signing key... it dies with
      the Cell").
    - `CellBootstrap.environment()` returns exactly the `HIVEMIND_*` keys
      `images/base-ubuntu/README.md`'s "Runtime configuration" table documents as required
      (`HIVEMIND_COMB_SHIELD`, the Cell's own tier, among them since roadmap step 10.3a), plus
      `HIVEMIND_SOCKS_PROXY_URL` only when `endpoint.socks_proxy_url` is set, and
      `HIVEMIND_RESERVATION` whenever `cell_endpoint` chose the endpoint, as every backend does:
      the Cell reports that reservation as its capacity, never the host's figures it would probe.
    - A Cell minted for NIGHT_VEIL through `cell_endpoint` always dials its Night Veil link's
      v3 `.onion` URL through that link's loopback SOCKS proxy, and is told its tier, or is never
      minted at all.
    - `HIVEMIND_PROVIDERS`/`HIVEMIND_SLOTS` (roadmap step 8.x's own gap, closed by this dispatch):
      rendered only when `endpoint.providers`/`.slots` are non-empty, so a Cell provisioned with no
      table at all (every pre-existing caller, and `hivemind.hive.backends.fake`'s own e2e slice)
      renders exactly the same environment as before this change -- `hivemind.cli.in_cell.
      providers.build_in_cell_provider_registry` keys its fake-vs-real choice on that same
      emptiness. Every provider's own API key, when it has one, rides a separate
      `HIVEMIND_<NAME>_API_KEY` variable (`endpoint.provider_api_keys`), never inside the JSON --
      `hivemind.hive.backends.provider_table`'s own module docstring explains why.

See Also:
    - docs/adr/0027-virtual-cells-connect-outbound-only-and-boot-a-warden.md for the connection
      direction and per-Cell signing key this module implements.
    - images/base-ubuntu/README.md "Runtime configuration" for the environment variable table
      `environment()` renders.
    - hivemind.cli.in_cell.config for the in-Cell side of this same contract: `InCellEnv` and
      `build_runtime_config` read back exactly what `environment()` writes.
    - hivemind.hive.backends.fake for FakeReadinessGate, this Protocol's in-memory implementation.
    - hivemind.hive.backends.docker for the first CellBackend built on this module.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Protocol
from urllib.parse import urlsplit

from pydantic import SecretStr

from hivemind.cell import CellCapabilities, CombShieldLevel
from hivemind.forage import ForageCapacity
from hivemind.hive.backends.provider_table import (
    CellProviderSpec,
    CellSlotSpec,
    render_providers_json,
    render_slots_json,
)
from hivemind.hive.errors import CellProvisionError
from hivemind.hive.models import CellReservation, VirtualCellSpec
from waggle.clock import Clock
from waggle.ids import CellId, HiveId, NodeId, new_cell_id
from waggle.signing import Ed25519Signer, public_key_hex
from waggle.transport.socks import SocksProxy
from waggle.uris import check_waggle_uri, is_onion_service_host

# The exact HIVEMIND_* names images/base-ubuntu's entry point reads (its own README's "Runtime
# configuration" table); named once here so a typo in the rendered dict fails at review time
# rather than only when a real Cell fails to start.
_ENV_QUEEN_WAGGLE_URL = "HIVEMIND_QUEEN_WAGGLE_URL"
_ENV_CELL_ID = "HIVEMIND_CELL_ID"
_ENV_HIVE_ID = "HIVEMIND_HIVE_ID"
_ENV_QUEEN_NODE_ID = "HIVEMIND_QUEEN_NODE_ID"
_ENV_CELL_SIGNING_KEY = "HIVEMIND_CELL_SIGNING_KEY"
_ENV_QUEEN_VERIFY_KEY = "HIVEMIND_QUEEN_VERIFY_KEY"
_ENV_SOCKS_PROXY_URL = "HIVEMIND_SOCKS_PROXY_URL"
_ENV_COMB_SHIELD = "HIVEMIND_COMB_SHIELD"  # The Cell's own tier, so its floors see it (10.3a).
# What the backend reserves for the Cell (hivemind.hive.models.CellReservation, as JSON): the
# capacity it reports, since a container's own probe would read the host's cores, memory and load.
_ENV_RESERVATION = "HIVEMIND_RESERVATION"
_ENV_PROVIDERS = "HIVEMIND_PROVIDERS"
_ENV_SLOTS = "HIVEMIND_SLOTS"
# Reuses HIVEMIND_LLM_OFFLINE, the exact name hivemind.manifest.env.EnvOverrides/InCellEnv already
# read on the Hive Stand and in-Cell sides respectively: one flag name, one meaning, everywhere.
_ENV_LLM_OFFLINE = "HIVEMIND_LLM_OFFLINE"

_WEBSOCKET_SCHEME = "ws://"  # Tor authenticates and encrypts an onion service end to end.
_SCHEME_SEPARATOR = "://"  # A hidden-service address written as a whole URL keeps its scheme.

__all__ = [
    "CellBootstrap",
    "CellReadyInfo",
    "NightVeilLink",
    "QueenEndpoint",
    "ReadinessGate",
    "cell_endpoint",
    "mint_cell_bootstrap",
    "night_veil_waggle_url",
]


def night_veil_waggle_url(hidden_service_address: str) -> str:
    """Return the Waggle URL a Night Veil Cell dials for the Hive Stand's hidden service.

    Args:
        hidden_service_address: `[security.tiers.NIGHT_VEIL] hidden_service_address`: a `.onion`
            host, with or without a port, or a whole `ws://` URL.

    Returns:
        The address unchanged when it already names a scheme; otherwise `ws://<address>`, since an
        onion service is already authenticated and encrypted by Tor itself.
    """
    if _SCHEME_SEPARATOR in hidden_service_address:
        return hidden_service_address
    return f"{_WEBSOCKET_SCHEME}{hidden_service_address}"


@dataclass(frozen=True, slots=True)
class NightVeilLink:
    """How a Night Veil Cell reaches the Queen: her hidden service, through its own Tor proxy.

    Attributes:
        waggle_url: The Hive Stand's hidden-service Waggle URL (`night_veil_waggle_url`).
        socks_proxy_url: The Tor SOCKS proxy on the Cell's own loopback
            (`[security.tiers.NIGHT_VEIL] tor_socks`, e.g. `socks5h://127.0.0.1:9050`).
    """

    waggle_url: str
    socks_proxy_url: str

    @classmethod
    def from_profile(cls, hidden_service_address: str, tor_socks: str) -> NightVeilLink | None:
        """Build the link from the Night Veil tier profile, or None when it is not configured.

        Args:
            hidden_service_address: The profile's hidden-service address; empty when unset.
            tor_socks: The profile's Tor SOCKS proxy URL; empty when unset.

        Returns:
            The link, or None when either value is empty: a Hive with no hidden service or no
            Tor proxy has no way to reach a Night Veil Cell, and says so by having no link.
        """
        if not hidden_service_address or not tor_socks:
            return None
        return cls(
            waggle_url=night_veil_waggle_url(hidden_service_address), socks_proxy_url=tor_socks
        )


@dataclass(frozen=True, slots=True)
class QueenEndpoint:
    """Where the Queen is and who she is, exactly as a freshly provisioned Cell must see her.

    Built by a backend's composition root (a later phase's `cli/`) from the Hive Manifest and
    handed to every `CellBackend` that needs to bring a Cell up; never constructed by a backend
    itself, since the same Queen is reachable the same way from every Cell a given backend makes.

    Attributes:
        waggle_url: The Queen's Waggle URL, reachable FROM inside a Cell -- not necessarily the
            same URL a Real Cell or an operator's device would use (a Docker Cell reaches it
            through the backend's own host-gateway wiring, `hivemind.hive.backends.docker.network`).
        queen_node_id: The node id the Queen signs its own frames as, so a Cell's Verifier knows
            whose signature to check (`waggle.signing.Ed25519Verifier` is keyed by node_id).
        queen_verify_key_hex: The Queen's own Ed25519 public key, hex-encoded.
        socks_proxy_url: A SOCKS proxy Waggle should dial through, once Night Veil (roadmap step
            5.7a) routes a Cell's link over Tor; None for every other Cell.
        providers: This Hive's own `[llm.providers]` table, decoupled (`hivemind.hive.backends.
            provider_table.CellProviderSpec`) and with every loopback `base_url` already rewritten
            to be reachable from inside the Cell (`hivemind.cli.compose.virtual_cells`'s own job).
            Empty for a Cell that should keep resolving every model slot to the scriptable fake
            (`hivemind.cli.in_cell.providers`'s own module docstring) -- every pre-existing caller.
        slots: This Hive's own `[llm.slots]` table, decoupled the same way
            (`hivemind.hive.backends.provider_table.CellSlotSpec`). Empty exactly when `providers`
            is: a Cell with nothing to resolve a slot against has no bindings to carry either.
        provider_api_keys: Every configured provider's own API key, when it has one, keyed by the
            exact `HIVEMIND_<NAME>_API_KEY`-shaped variable name `providers`' own `api_key_env`
            names -- never folded into `providers` itself (`hivemind.hive.backends.provider_table`'s
            own module docstring: a key value never rides the JSON blob). A `SecretStr` end to end;
            `environment()` is the only place any of these is unwrapped.
        llm_offline: This Hive's own `[llm] offline` flag, carried through so the Cell's own
            `ProviderRegistry` enforces the identical policy the Hive Stand does (roadmap step
            8.x's own gap: a gateway-host base URL is not loopback from the Cell's own point of
            view, so the Cell's offline check needs the same gateway carve-out
            `waggle.uris.is_virtual_cell_gateway_host` gives the Hive Stand's link validation).
        night_veil: How a Night Veil Cell reaches the Queen instead (roadmap step 10.3a): her
            hidden service through its own Tor proxy; None when this Hive configured none, in
            which case no Night Veil Cell is ever minted (`cell_endpoint`).
        comb_shield: The tier of the Cell this endpoint was chosen for (`cell_endpoint` sets it
            from the Cell's spec), rendered as `HIVEMIND_COMB_SHIELD` so the Cell's own floors
            see it; MEADOW, the default tier, until then.
        reservation: What the backend reserves for that Cell (`cell_endpoint` sets it from the
            Cell's spec, alongside its tier), rendered as `HIVEMIND_RESERVATION` so the Cell
            reports it as its capacity; None until then.
        cell_id: The id that Cell's lifecycle minted for it as provisioning began
            (`cell_endpoint` sets it from the Cell's spec), which `mint_cell_bootstrap` then
            gives the Cell; None mints a fresh one there.
    """

    waggle_url: str
    queen_node_id: NodeId
    queen_verify_key_hex: str
    socks_proxy_url: str | None = None
    providers: tuple[CellProviderSpec, ...] = ()
    slots: tuple[CellSlotSpec, ...] = ()
    provider_api_keys: Mapping[str, SecretStr] = field(default_factory=dict)
    llm_offline: bool = False
    night_veil: NightVeilLink | None = None
    comb_shield: CombShieldLevel = CombShieldLevel.MEADOW
    reservation: CellReservation | None = None
    cell_id: CellId | None = None


@dataclass(frozen=True, slots=True)
class CellBootstrap:
    """A freshly minted Cell identity, ready to render as a Virtual Cell's runtime environment.

    Built by `mint_cell_bootstrap`, never constructed directly: the constructor is the one place
    that mints both the `CellId` and the keypair together, so the two can never drift apart.
    """

    cell_id: CellId
    hive_id: HiveId
    endpoint: QueenEndpoint
    private_key_hex: SecretStr
    public_key_hex: str

    def environment(self) -> Mapping[str, str]:
        """Render exactly the HIVEMIND_* variables images/base-ubuntu's entry point reads.

        Returns:
            A mapping ready to hand a backend's container/VM creation call as the Cell's own
            environment. `HIVEMIND_SOCKS_PROXY_URL` is present only when `endpoint.socks_proxy_url`
            is set; `HIVEMIND_PROVIDERS`/`HIVEMIND_SLOTS` (plus every provider's own
            `HIVEMIND_<NAME>_API_KEY`, and `HIVEMIND_LLM_OFFLINE`) only when `endpoint.providers` is
            non-empty (this method's own Key invariants); every other key is always present.
        """
        env = {
            _ENV_QUEEN_WAGGLE_URL: self.endpoint.waggle_url,
            _ENV_CELL_ID: str(self.cell_id),
            _ENV_HIVE_ID: str(self.hive_id),
            _ENV_QUEEN_NODE_ID: str(self.endpoint.queen_node_id),
            # SecretStr's whole purpose is to keep this value out of every repr and log line
            # (codingrules 13/15); this is the one call site allowed to unwrap it, because the
            # Cell that receives it is the key's own owner, not a log or a trail event.
            _ENV_CELL_SIGNING_KEY: self.private_key_hex.get_secret_value(),
            _ENV_QUEEN_VERIFY_KEY: self.endpoint.queen_verify_key_hex,
            # Roadmap step 10.3a: the Cell's tier comes from here, never assumed in the Cell.
            _ENV_COMB_SHIELD: self.endpoint.comb_shield.value,
        }
        # Its capacity, taken from its spec rather than probed (module docstring's invariant).
        if self.endpoint.reservation is not None:
            env[_ENV_RESERVATION] = self.endpoint.reservation.model_dump_json()
        # Optional: a base-ubuntu Cell never sets a SOCKS proxy (images/base-ubuntu/README.md),
        # and InCellEnv.socks_proxy_url already treats an absent variable as "no proxy", so
        # omitting the key rather than sending an empty string keeps both sides agreeing on None.
        if self.endpoint.socks_proxy_url is not None:
            env[_ENV_SOCKS_PROXY_URL] = self.endpoint.socks_proxy_url
        if self.endpoint.providers:
            env[_ENV_PROVIDERS] = render_providers_json(self.endpoint.providers)
            env[_ENV_SLOTS] = render_slots_json(self.endpoint.slots)
            # Every provider's own key rides its own variable, never the JSON above (module
            # docstring's own Key invariant); get_secret_value() is safe here for the same reason
            # the signing key above is: the Cell is this key's own intended owner.
            for var_name, secret in self.endpoint.provider_api_keys.items():
                env[var_name] = secret.get_secret_value()
            # Only rendered alongside a real provider table: with none, hivemind.cli.in_cell.
            # providers never builds a registry that would consult it at all (module docstring).
            if self.endpoint.llm_offline:
                env[_ENV_LLM_OFFLINE] = "true"
        return env


@dataclass(frozen=True, slots=True)
class CellReadyInfo:
    """What a Cell reported about itself once ready: its capabilities and promised capacity.

    Returned by `ReadinessGate.wait_ready`; a backend's `provision()` reads both fields straight
    onto the `hivemind.cell.Cell` it returns (`capabilities` and `capacity` respectively).
    """

    capabilities: CellCapabilities
    capacity: ForageCapacity


class ReadinessGate(Protocol):
    """Register a Cell's public key, then learn when that Cell has actually announced itself.

    The real, Queen-side implementation (a later roadmap step) resolves a Cell's `CellReady` --
    matched back to `cell_id` once the Cell's freshly-minted `node_id` arrives on its first frame
    -- and its first `CellHeartbeat` into a `CellReadyInfo`; this Protocol only defines that seam,
    so `hivemind.hive.backends.docker` (and later `qemu`/`cloud` backends) can provision against
    it before the Queen side exists. Implementations must be safe to call concurrently: a backend
    may provision several Cells at once.
    """

    async def expect(self, cell_id: CellId, verify_key_hex: str) -> None:
        """Register `cell_id`'s public key before its container/VM is even created.

        Must be called before the backend starts the Cell, so the Queen can verify the very first
        signed frame it sends (ADR-0027).

        Args:
            cell_id: The Cell this key belongs to, minted by `mint_cell_bootstrap`.
            verify_key_hex: That Cell's Ed25519 public key, hex-encoded
                (`CellBootstrap.public_key_hex`).
        """
        ...

    async def wait_ready(self, cell_id: CellId, timeout_s: float) -> CellReadyInfo:
        """Block until `cell_id` has sent a signed `CellReady` and its first `CellHeartbeat`.

        Args:
            cell_id: The Cell to wait for; must already have been passed to `expect`.
            timeout_s: Seconds to wait before giving up. Matches
                `hivemind.hive.models.VirtualCellSpec.ready_timeout_s`.

        Returns:
            The Cell's reported capabilities and promised ForageCapacity.

        Raises:
            TimeoutError: `cell_id` did not report ready within `timeout_s`. Callers translate
                this into their own typed provisioning error (e.g.
                `hivemind.hive.errors.CellProvisionError`); this Protocol stays backend-agnostic.
        """
        ...

    async def forget(self, cell_id: CellId) -> None:
        """Drop any registration or pending wait for `cell_id`.

        Idempotent: forgetting a `cell_id` that was never `expect`-ed, or was already forgotten,
        is a no-op -- a backend calls this on every cleanup path, successful or not.

        Args:
            cell_id: The Cell to stop tracking.
        """
        ...


def mint_cell_bootstrap(hive_id: HiveId, endpoint: QueenEndpoint, clock: Clock) -> CellBootstrap:
    """Mint a fresh Ed25519 keypair, and the CellId, for one Cell about to be provisioned.

    Args:
        hive_id: The Hive the new Cell belongs to.
        endpoint: Where and who the Queen is, as this Cell must reach her; its `cell_id`, when
            `cell_endpoint` set one, is the id the Cell gets instead of a fresh one.
        clock: Source of a freshly minted CellId's timestamp.

    Returns:
        A CellBootstrap ready for `ReadinessGate.expect` and, once a backend has created the
        Cell's infrastructure, `environment()`.
    """
    # One signer per Cell, generated fresh and never persisted beyond this process (ADR-0027:
    # "each Cell gets its own signing key, minted at provision... it dies with the Cell").
    signer = Ed25519Signer.generate()
    # The lifecycle's own id when it minted one as provisioning began (cell_endpoint carried it):
    # its first record already names that id, so the Cell must boot as that very Cell.
    cell_id = endpoint.cell_id if endpoint.cell_id is not None else new_cell_id(clock)
    return CellBootstrap(
        cell_id=cell_id,
        hive_id=hive_id,
        endpoint=endpoint,
        private_key_hex=SecretStr(signer.private_key_bytes.hex()),
        public_key_hex=public_key_hex(signer.public_key_bytes),
    )


def cell_endpoint(endpoint: QueenEndpoint, spec: VirtualCellSpec, backend: str) -> QueenEndpoint:
    """Return the endpoint a Cell provisioned from `spec` dials: ordinary, or its Night Veil link.

    Args:
        endpoint: The backend's endpoint, possibly carrying a Night Veil link.
        spec: The Cell about to be provisioned; its `comb_shield` decides.
        backend: The provisioning backend's name, for the refusal.

    Returns:
        `endpoint` at the Cell's tier, carrying its reservation and the id its spec was minted
        (if any), for MEADOW and PROPOLIS; for NIGHT_VEIL, likewise, a copy whose Waggle URL is
        the hidden service and whose SOCKS proxy is the Tor proxy (and which carries no link of
        its own, so nothing downstream can choose again).

    Raises:
        CellProvisionError: The Cell is NIGHT_VEIL and `endpoint` carries no Night Veil link, or
            one the Cell could never dial (not a v3 onion service, or a proxy that is not a
            loopback `socks5h`/`socks4a` one); nothing has been created.
    """
    tier = spec.comb_shield
    # The Cell's own figures ride with its tier, and so does the id its lifecycle minted: all are
    # its spec's, and all the Cell must be told rather than find out (or mint) for itself.
    ours = dataclasses.replace(
        endpoint, comb_shield=tier, reservation=CellReservation.of(spec), cell_id=spec.cell_id
    )
    if tier is not CombShieldLevel.NIGHT_VEIL:
        return ours
    link = endpoint.night_veil
    if link is None:
        raise CellProvisionError(
            backend,
            spec.image,
            "a NIGHT_VEIL Cell dials the Queen only through her Tor hidden service, and no "
            "[security.tiers.NIGHT_VEIL] hidden_service_address and tor_socks are configured",
        )
    problem = _link_problem(link)
    if problem is not None:
        raise CellProvisionError(backend, spec.image, problem)
    return dataclasses.replace(
        ours, waggle_url=link.waggle_url, socks_proxy_url=link.socks_proxy_url, night_veil=None
    )


def _link_problem(link: NightVeilLink) -> str | None:
    """Say why a Night Veil Cell could never dial `link`, or None when it could."""
    # The same two rules the Cell's own transport applies, checked before anything exists.
    host = urlsplit(link.waggle_url).hostname or ""
    if not is_onion_service_host(host):
        return (
            f"the Night Veil hidden service {link.waggle_url!r} is not a v3 onion service "
            "([security.tiers.NIGHT_VEIL] hidden_service_address)"
        )
    try:
        check_waggle_uri(link.waggle_url)
        SocksProxy.parse(link.socks_proxy_url)
    except ValueError as exc:
        return f"the Night Veil link cannot be dialled: {exc}"
    return None
