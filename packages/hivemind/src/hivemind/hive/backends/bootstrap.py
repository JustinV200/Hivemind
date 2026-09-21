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
    - `mint_cell_bootstrap` mints a fresh `CellId` and a fresh Ed25519 keypair on every call: keys
      are per Cell, never reused (ADR-0027: "each Cell gets its own signing key... it dies with
      the Cell").
    - `CellBootstrap.environment()` returns exactly the `HIVEMIND_*` keys
      `images/base-ubuntu/README.md`'s "Runtime configuration" table documents as required, plus
      `HIVEMIND_SOCKS_PROXY_URL` only when `endpoint.socks_proxy_url` is set.

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

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

from pydantic import SecretStr

from hivemind.cell import CellCapabilities
from hivemind.forage import ForageCapacity
from waggle.clock import Clock
from waggle.ids import CellId, HiveId, NodeId, new_cell_id
from waggle.signing import Ed25519Signer, public_key_hex

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

__all__ = [
    "CellBootstrap",
    "CellReadyInfo",
    "QueenEndpoint",
    "ReadinessGate",
    "mint_cell_bootstrap",
]


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
    """

    waggle_url: str
    queen_node_id: NodeId
    queen_verify_key_hex: str
    socks_proxy_url: str | None = None


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
            is set; every other key is always present.
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
        }
        # Optional: a base-ubuntu Cell never sets a SOCKS proxy (images/base-ubuntu/README.md),
        # and InCellEnv.socks_proxy_url already treats an absent variable as "no proxy", so
        # omitting the key rather than sending an empty string keeps both sides agreeing on None.
        if self.endpoint.socks_proxy_url is not None:
            env[_ENV_SOCKS_PROXY_URL] = self.endpoint.socks_proxy_url
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
    """Mint a fresh CellId and Ed25519 keypair for one Cell about to be provisioned.

    Args:
        hive_id: The Hive the new Cell belongs to.
        endpoint: Where and who the Queen is, as this Cell must reach her.
        clock: Source of the freshly minted CellId's timestamp.

    Returns:
        A CellBootstrap ready for `ReadinessGate.expect` and, once a backend has created the
        Cell's infrastructure, `environment()`.
    """
    # One signer per Cell, generated fresh and never persisted beyond this process (ADR-0027:
    # "each Cell gets its own signing key, minted at provision... it dies with the Cell").
    signer = Ed25519Signer.generate()
    return CellBootstrap(
        cell_id=new_cell_id(clock),
        hive_id=hive_id,
        endpoint=endpoint,
        private_key_hex=SecretStr(signer.private_key_bytes.hex()),
        public_key_hex=public_key_hex(signer.public_key_bytes),
    )
