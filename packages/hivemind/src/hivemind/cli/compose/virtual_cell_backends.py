"""Construct one Virtual Cell CellBackend, lazily, with the QueenEndpoint it needs to provision.

Split out of `hivemind.cli.compose.virtual_cells` for its own line budget (codingrules 5.1): that
module owns the overall `build_virtual_cells` orchestration; this one concept -- how "fake",
"docker" and "qemu" each build their own `hivemind.hive.backends.base.CellBackend` and the
`hivemind.hive.backends.bootstrap.QueenEndpoint` every Cell they provision needs -- is
`_build_registry`'s own job, called once from `virtual_cells.build_virtual_cells`.

`_endpoint_for` is where a Cell's dial-back address, provider/slot table and Night Veil SOCKS
proxy all come together; `_listener_url` is the one place an offline command (`hive cells
inspect`/`destroy`/`abscond`, `cli/readback/virtual*.py`) is let through even though it never
starts `CellListener` -- see its own docstring.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside `hivemind.cli.compose`. Called by
    `hivemind.cli.compose.virtual_cells.build_virtual_cells` (via `_build_registry`, this
    module's one public entry point). Calls into `hivemind.cell` (CombShieldLevel),
    `hivemind.cli.compose.virtual_cell_providers` (cell_providers, cell_slots,
    provider_api_keys), `hivemind.common.errors` (ConfigurationError), `hivemind.hive`
    (BackendRegistry, build_docker_backend, build_qemu_backend), `hivemind.hive.backends.
    bootstrap` (QueenEndpoint), `hivemind.hive.backends.docker` (DockerCellBackend,
    SdkDockerClient), `hivemind.hive.backends.fake` (FakeCellBackend), `hivemind.hive.backends.
    qemu` (ProcessQemuRunner, QemuBackendConfig, QemuCellBackend, USER_NET_HOST_ALIAS),
    `hivemind.manifest` (HiveManifest), `hivemind.manifest.schema.placement`
    (VirtualCellsSection), `hivemind.queen.cell_gate` (CellListener, QueenReadinessGate) and
    waggle only.

Key invariants:
    - `_build_registry` registers exactly one backend, `ctx.section.backend`'s own selected name
      -- never "fake" alongside a real one (see its own docstring: a real Docker run found
      placement picking "fake" first by `registry.names()` order in a Docker-configured Hive,
      provisioning a fake Cell nothing ever connects to).
    - `_listener_url` never masks a real failure: an offline command that never starts
      `CellListener` gets a documented placeholder (`_UNSTARTED_LISTENER_PLACEHOLDER`) instead of
      the listener's own `RuntimeError`, but a caller that DID try to provision through it fails
      at the Cell's own dial-out instead -- the correct failure, not a hidden one.

See Also:
    - hivemind.cli.compose.virtual_cells for build_virtual_cells, this module's one caller, and
      docker_gateway_url/_night_veil_socks_proxy_url, re-exported there for its own tests.
    - docs/adr/0027-virtual-cells-connect-outbound-only-and-boot-a-warden.md for QueenEndpoint and
      why a Cell's own dial-back address needs backend-specific resolution.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit

from hivemind.cell import CombShieldLevel
from hivemind.cli.compose.night_veil import night_veil_link
from hivemind.cli.compose.virtual_cell_providers import (
    cell_providers,
    cell_slots,
    provider_api_keys,
)
from hivemind.common.errors import ConfigurationError
from hivemind.hive import BackendRegistry, build_docker_backend, build_qemu_backend
from hivemind.hive.backends.bootstrap import QueenEndpoint
from hivemind.hive.backends.docker.backend import DockerCellBackend
from hivemind.hive.backends.docker.sdk_client import SdkDockerClient
from hivemind.hive.backends.fake import FakeCellBackend
from hivemind.hive.backends.qemu.backend import QemuBackendConfig, QemuCellBackend
from hivemind.hive.backends.qemu.network import USER_NET_HOST_ALIAS
from hivemind.hive.backends.qemu.process_runner import ProcessQemuRunner
from hivemind.manifest import HiveManifest
from hivemind.manifest.schema.placement import VirtualCellsSection
from hivemind.queen.cell_gate import CellListener, QueenReadinessGate
from waggle.clock import Clock
from waggle.ids import NodeId
from waggle.signing import Ed25519Signer, public_key_hex
from waggle.uris import check_waggle_uri

_DOCKER_GATEWAY_HOST = "host.docker.internal"  # docker_gateway_url's own rewrite target.
# A loopback URI that passes check_waggle_uri unmodified, used only when an offline command
# (module docstring) needs a QueenEndpoint before CellListener.start() has ever run; never dialled.
_UNSTARTED_LISTENER_PLACEHOLDER = "ws://127.0.0.1:0"

__all__ = ["build_registry", "docker_gateway_url", "night_veil_socks_proxy_url"]


@dataclass(frozen=True, slots=True)
class RegistryContext:
    """Everything `build_registry`'s own lazy `docker`/`qemu` factories need (codingrules 5.1)."""

    manifest: HiveManifest
    section: VirtualCellsSection
    gate: QueenReadinessGate
    listener: CellListener
    queen_signer: Ed25519Signer
    queen_node_id: NodeId
    environ: Mapping[str, str]


def build_registry(ctx: RegistryContext, clock: Clock) -> BackendRegistry:
    """Register exactly one backend: `ctx.section.backend`'s own selected name.

    A real Docker run found the previous "always register fake, plus the selected backend" shape
    provisioning a fake Cell nothing ever connects to: `hivemind.queen.placement` can pick ANY
    registered backend, in `registry.names()` order, and "fake" registered first in a
    Docker-configured Hive won that pick. `hivemind.hive.lifecycle.CellLifecycle.
    virtual_backend_candidates` -- existing code, not this dispatch's own -- calls
    `BackendRegistry.get` for *every* registered name to read its own capabilities, on every
    placement snapshot (`hivemind.queen.dispatcher.snapshot.build_inventory`, every tick), so a
    backend that is registered but never wanted is not just idle: it is a live placement option.
    `hivemind.cli.compose.virtual_cells.build_virtual_cells` already returns `None` when
    `section.backend` is `None`, so it is always exactly one of "fake"/"docker"/"qemu" here.
    """
    registry = BackendRegistry()
    if ctx.section.backend == "fake":
        # Roadmap step 5's own e2e slice: pass the listener's own QueenEndpoint so the fake
        # backend mints a real CellBootstrap on every provision() (hivemind.hive.backends.fake's
        # own module docstring, "endpoint" constructor argument) -- otherwise self.bootstraps
        # stays empty and nothing (a test harness standing in for a real container) has an
        # identity to dial the Queen's own listener back with. A *callable*
        # (`lambda: _fake_backend_endpoint(ctx)`), resolved fresh by FakeCellBackend on every
        # provision() call, never eagerly here: `hivemind.hive.lifecycle.CellLifecycle.reconcile`
        # (run_hive's own module docstring: called before listener.start()) already forces this
        # "fake" factory to run once, through BackendRegistry.get's own construct-once-and-cache
        # contract, before the listener has a real URL to give -- an eagerly-resolved endpoint
        # would bake "not started yet" into this cached instance forever, and every Virtual Cell
        # this Hive ever provisions would silently never mint a bootstrap. `_fake_backend_endpoint`
        # falls back to None when the listener still has not started (e.g. a caller that seeds the
        # fake backend directly, never running a real Hive), matching every pre-existing caller's
        # own endpoint-less behaviour. `gate=ctx.gate`: the same real ReadinessGate every other
        # backend factory below already receives, so provision() can call gate.expect() the way a
        # real backend does (ADR-0027); unused whenever endpoint resolves to None.
        registry.register(
            "fake",
            lambda: FakeCellBackend(
                clock, endpoint=lambda: _fake_backend_endpoint(ctx), gate=ctx.gate
            ),
        )
    elif ctx.section.backend == "docker":
        registry.register("docker", lambda: _build_docker(ctx, clock))
    elif ctx.section.backend == "qemu":
        registry.register("qemu", lambda: _build_qemu(ctx, clock))
    return registry


def _build_docker(ctx: RegistryContext, clock: Clock) -> DockerCellBackend:
    """Construct the real DockerCellBackend; only ever called once "docker" is actually needed."""
    endpoint = _endpoint_for(
        ctx, docker=True, gateway_host=_DOCKER_GATEWAY_HOST, require_started_listener=False
    )
    factory = build_docker_backend(
        SdkDockerClient(), ctx.gate, endpoint, clock, max_cells=ctx.section.max_cells
    )
    return factory()


def _build_qemu(ctx: RegistryContext, clock: Clock) -> QemuCellBackend:
    """Construct the real QemuCellBackend; only ever called once "qemu" is actually needed."""
    section = ctx.section
    if section.qemu_base_image is None or section.qemu_vm_root is None:
        raise ConfigurationError(
            "[virtual_cells] backend = 'qemu' requires qemu_base_image and qemu_vm_root to both "
            "be set."
        )
    vm_root = ctx.manifest.resolve_path(section.qemu_vm_root)
    base_image = ctx.manifest.resolve_path(section.qemu_base_image)
    config = QemuBackendConfig(base_image=base_image, vm_root=vm_root, max_cells=section.max_cells)
    endpoint = _endpoint_for(
        ctx, docker=False, gateway_host=USER_NET_HOST_ALIAS, require_started_listener=False
    )
    factory = build_qemu_backend(
        ProcessQemuRunner(vm_root), ctx.gate, endpoint, clock, config=config
    )
    return factory()


def _fake_backend_endpoint(ctx: RegistryContext) -> QueenEndpoint | None:
    """Best-effort QueenEndpoint for the "fake" backend factory (module docstring's own note).

    Roadmap step 5's own e2e slice: an in-process test harness standing in for a real container
    dials the Queen back the same way a real backend's Cell would, so it needs a real
    `CellBootstrap` -- which `hivemind.hive.backends.fake.FakeCellBackend` only mints when its own
    `endpoint` constructor argument is set. That needs `ctx.listener.uri`, which only resolves once
    `hivemind.cli.compose.hive.run_hive` has called `listener.start()`; a caller that seeds the fake
    backend directly, without ever running a real Hive (e.g. a `hive cells` CLI readback test),
    never starts that listener at all. Falling back to `None` there keeps `FakeCellBackend`'s own
    pre-existing, endpoint-less behaviour (no bootstrap minted) exactly as before this branch.

    Args:
        ctx: This backend's own registry context.

    Returns:
        A real `QueenEndpoint` once the listener has started; `None` otherwise.
    """
    try:
        return _endpoint_for(ctx, docker=False)
    except RuntimeError:
        # listener.start() has not run yet (this function's own docstring): fall back to no
        # bootstrap, matching every caller of the "fake" backend factory from before this branch.
        # (require_started_listener stays at its True default here, deliberately: unlike
        # _build_docker/_build_qemu, this caller wants the RuntimeError to reach this except.)
        return None


def _endpoint_for(
    ctx: RegistryContext,
    *,
    docker: bool,
    comb_shield: CombShieldLevel = CombShieldLevel.MEADOW,
    gateway_host: str | None = None,
    require_started_listener: bool = True,
) -> QueenEndpoint:
    """Resolve the Queen's own dial-back URL, lazily (module docstring): `listener.uri` by then.

    Args:
        ctx: This backend's own registry context.
        docker: Whether the loopback rewrite (`docker_gateway_url`) applies to the Waggle URL.
        comb_shield: The tier the Cell(s) reached through this endpoint run at; only NIGHT_VEIL
            ever sets `socks_proxy_url` (see `night_veil_socks_proxy_url`). MEADOW by default,
            matching every pre-roadmap-5.7a caller's own behaviour.
        gateway_host: The host a Cell reaches the Hive Stand through, for rewriting every
            provider's own `base_url` (`cell_providers`); None (the default, `_fake_backend_
            endpoint`'s own caller) leaves every base_url unchanged (same-process backend).
        require_started_listener: True (the default, `_fake_backend_endpoint`'s own caller) lets
            the listener's own "no port until start()" `RuntimeError` propagate; False
            (`_build_docker`/`_build_qemu`'s own fix for an offline command against a
            Docker/QEMU-configured manifest, module docstring) falls back to a documented
            placeholder instead, so those commands can still construct the backend.
    """
    url = ctx.section.advertise_url
    if url is None:
        url = _listener_url(ctx, docker=docker, required=require_started_listener)
    validated = check_waggle_uri(url, allow_virtual_cell_gateway_host=True)
    return QueenEndpoint(
        waggle_url=validated,
        queen_node_id=ctx.queen_node_id,
        queen_verify_key_hex=public_key_hex(ctx.queen_signer.public_key_bytes),
        socks_proxy_url=night_veil_socks_proxy_url(ctx.manifest, comb_shield),
        providers=cell_providers(ctx.manifest, gateway_host),
        slots=cell_slots(ctx.manifest),
        provider_api_keys=provider_api_keys(ctx.manifest, ctx.environ),
        llm_offline=ctx.manifest.llm.offline,
        # Roadmap step 10.3a: how a Night Veil Cell reaches the Queen instead, chosen per Cell
        # at provisioning (`hivemind.hive.backends.bootstrap.cell_endpoint`).
        night_veil=night_veil_link(ctx.manifest),
    )


def _listener_url(ctx: RegistryContext, *, docker: bool, required: bool) -> str:
    """Return `ctx.listener.uri`, rewritten through `docker_gateway_url` when `docker`.

    Args:
        ctx: This backend's own registry context.
        docker: Whether `docker_gateway_url` applies.
        required: True propagates the listener's own `RuntimeError` when `start()` has not run
            yet; False falls back to `_UNSTARTED_LISTENER_PLACEHOLDER` instead (`_endpoint_for`'s
            own docstring explains both callers).

    Raises:
        RuntimeError: `required` is True and the listener has not started.
    """
    try:
        url = ctx.listener.uri
    except RuntimeError:
        if required:
            raise
        return _UNSTARTED_LISTENER_PLACEHOLDER
    return docker_gateway_url(url) if docker else url


def docker_gateway_url(listener_uri: str) -> str:
    """Rewrite a listener URI so a Docker container can dial it back.

    Docker Desktop and modern Docker Engine installs both resolve `host.docker.internal` to the
    host from inside a container; a bare `127.0.0.1`/`localhost` inside a container means the
    container itself, never the host (module docstring). A wildcard bind (`0.0.0.0`, `::`) is
    rewritten too: it is what `[virtual_cells] listen_host` must be for a container to reach the
    listener at all, and a Cell handed `ws://0.0.0.0:<port>` dials itself and is refused (the
    first real Docker run did exactly that).

    Args:
        listener_uri: `CellListener.uri`, e.g. `"ws://0.0.0.0:54321"`.

    Returns:
        The same URI with a loopback or wildcard host replaced by `host.docker.internal`;
        unchanged if the host is already something else (an operator-set `advertise_url` wins
        over this entirely -- see `_endpoint_for`, this function's one caller).
    """
    parts = urlsplit(listener_uri)
    if parts.hostname not in _HOSTS_UNREACHABLE_FROM_A_CONTAINER:
        return listener_uri
    netloc = _DOCKER_GATEWAY_HOST if parts.port is None else f"{_DOCKER_GATEWAY_HOST}:{parts.port}"
    return urlunsplit(parts._replace(netloc=netloc))


# Hosts that name the listener's own machine from the host's point of view but the container
# itself from inside one: loopback, and the wildcard binds a reachable listener uses.
_HOSTS_UNREACHABLE_FROM_A_CONTAINER = frozenset({"127.0.0.1", "localhost", "0.0.0.0", "::"})  # noqa: S104  # SAFETY: matched, never bound, here.


def night_veil_socks_proxy_url(manifest: HiveManifest, comb_shield: CombShieldLevel) -> str | None:
    """Return this Hive's own `[security]` NIGHT_VEIL `tor_socks`, only for a NIGHT_VEIL endpoint.

    Roadmap step 5.7a: threads `hivemind.manifest.schema.security.TierProfile.tor_socks` onto
    `QueenEndpoint.socks_proxy_url`, the field a Night Veil Cell's own Waggle transport must route
    through instead of the VPN tunnel (ADR-0030: sharing the tunnel with the control link would
    let an observer at the tunnel's exit correlate anonymised work with a known Hive Stand
    address). Report item: `_build_docker`/`_build_qemu` still build one endpoint per *backend*,
    shared by every comb_shield that backend provisions, and neither calls `_endpoint_for` with
    `comb_shield=NIGHT_VEIL` yet -- nothing in this composition root provisions a NIGHT_VEIL Cell
    through a distinct backend instance today (the same gap `hivemind.cli.compose.virtual_cells.
    _fail_closed_night_veil_probe` documents for attestation); this function is ready for that
    call once one exists.

    Args:
        manifest: This Hive's own manifest; only `security.tiers` is read.
        comb_shield: The tier being provisioned for; every value but NIGHT_VEIL returns None.

    Returns:
        `tier.tor_socks` when a NIGHT_VEIL tier profile is configured with one; `None` otherwise
        (either `comb_shield` is not NIGHT_VEIL, or the operator has not set `tor_socks` yet --
        `hivemind.queen.placement.policy.check_night_veil` is what refuses placement for that).
    """
    if comb_shield is not CombShieldLevel.NIGHT_VEIL:
        return None
    # manifest.security.tiers is keyed by the wire enum (waggle.messages.CombShieldLevel), not
    # this hivemind-side mirror (hivemind.cell.tiers's own module docstring: "the hivemind-side
    # mirror of the same two wire enums"), so the lookup key needs converting first.
    tier = manifest.security.tiers.get(comb_shield.to_wire())
    if tier is None or not tier.tor_socks:
        return None
    return tier.tor_socks
