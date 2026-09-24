"""Provide FakeCellBackend and FakeReadinessGate: in-memory fakes for tests, demos and hive doctor.

FakeCellBackend implements `hivemind.hive.backends.base.CellBackend` over a plain in-memory table:
`provision()` mints a fresh CellId, builds a Cell whose capabilities reflect `spec.exoskeleton` (a
desktop image reports has_display, has_audio and has_browser; a terminal-only one does not) and
records the Cell tagged with `spec.hive_id` and `spec.labels` merged; `destroy()`, `pause()` and
`resume()` mutate that same table. Deterministic via an injected Clock (`waggle.clock`), never a
real clock or a real backend of any kind, so a test controls exactly how long provisioning appears
to take. Three switches simulate failure without touching real infrastructure:
`set_provision_failure` makes every subsequent `provision()` raise `CellProvisionError`;
`set_destroy_failure` does the same for `destroy()`; `set_provision_delay` makes `provision()`
await `clock.sleep()` before returning, so a test can prove a slow-to-ready Cell still finishes
within `spec.ready_timeout_s` -- or times out when the delay exceeds it. Every call is recorded
(`provision_calls`, `destroy_calls`, `pause_calls`, `resume_calls`, `egress_calls`) so a test can
assert on what was actually asked for. It is also the reference `EgressCutter` (roadmap step
10.6a): `cut_egress`/`restore_egress` flip a per-Cell flag (`egress_is_cut`) standing in for a
network whose only remaining destination is the Queen's Waggle listener, and the fake declares
`can_cut_egress` by default.

Roadmap step 5's own e2e slice (this branch) adds an optional `endpoint` constructor argument:
when given, `provision()` also mints a real `hivemind.hive.backends.bootstrap.CellBootstrap` via
`mint_cell_bootstrap` for the Cell it just built (recorded in `self.bootstraps`, keyed by the
minted `CellId`), the same identity a real backend would mint and pass into a real container's
environment. This is what lets an e2e test run a genuinely real in-Cell Warden
(`hivemind.cli.in_cell.main.run_in_cell_warden(bootstrap.environment(), clock)`) as an asyncio task
standing in for "the container", against a real Queen-side listener, with only the container
runtime itself faked. `endpoint=None` (the default) keeps every pre-existing caller's own
behaviour unchanged: no bootstrap is minted, and `self.bootstraps` stays empty.

FakeReadinessGate implements `hivemind.hive.backends.bootstrap.ReadinessGate` the same way:
`wait_ready` returns a default `CellReadyInfo` (or one arranged with `set_ready_info`) as soon as
it is called, unless `set_never_ready` is armed for that Cell, in which case it awaits the fake
clock's own `sleep(timeout_s)` and raises `TimeoutError`, exactly the shape a real gate's deadline
hits. It lives here, beside `FakeCellBackend`, rather than in its own module, because it is the
second (and, for now, only other) fake this package ships, and codingrules 5.2's "one file, one
concept" reads this package's pair of in-memory Protocol fakes as one concept: what lets
`hivemind.hive.backends.docker.DockerCellBackend` (roadmap step 5.4) and its tests run with no
real Docker daemon or Queen.

Both are shipped code, not test-only (codingrules section 14.4: "fakes live in src/ beside their
Protocol"), because `hive doctor` and demo paths use them too.

Fits into the Hive:
    Layer 3 (sources of Cells). Implements `hivemind.hive.backends.base.CellBackend` and
    `hivemind.hive.backends.bootstrap.ReadinessGate`; constructed directly by tests, demo scripts
    and `hive doctor`. Calls into hivemind.cell, hivemind.forage, hivemind.hive.backends.base,
    hivemind.hive.backends.bootstrap, hivemind.hive.cell_state, hivemind.hive.errors,
    hivemind.hive.models and waggle only.

Key invariants:
    - A Cell this backend returns always has kind == CellKind.VIRTUAL and access_level ==
      AccessLevel.FULL (Cell's own validator enforces the latter).
    - A failed provision() (via set_provision_failure, a readiness-timeout simulation, or a
      headroom breach) never adds an entry to this backend's table: list_cells never sees it
      (CellBackend's own key invariant).
    - destroy() always removes the record on success, so a destroyed Cell disappears from
      list_cells, matching what a real backend's own infrastructure would report.
    - pause()/resume() raise BackendCapabilityError whenever capabilities.can_pause is False,
      before touching the table or the recorded call lists.
    - cut_egress()/restore_egress() raise BackendCapabilityError whenever
      capabilities.can_cut_egress is False, before touching the table (the call is still
      recorded, like pause's).
    - FakeReadinessGate.forget() is idempotent: forgetting a Cell never `expect`-ed, or already
      forgotten, is a no-op, matching ReadinessGate's own documented contract.

See Also:
    - .claude/codingrules.md section 14.4 for "fakes live in src/, are shipped code."
    - hivemind.hive.backends.base for CellBackend, BackendCapabilities and VirtualCellRecord, the
      Protocol and value types FakeCellBackend implements and returns.
    - hivemind.hive.backends.bootstrap for ReadinessGate and CellReadyInfo, the Protocol and value
      type FakeReadinessGate implements and returns.
    - hivemind.llm.fake for FakeLLMProvider, the switches-and-call-recording pattern this mirrors.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from hivemind.cell import AccessLevel, Cell, CellCapabilities, CellKind, OsFamily
from hivemind.forage import ForageCapacity, HostCapacity
from hivemind.hive.backends.base import BackendCapabilities, VirtualCellRecord
from hivemind.hive.backends.bootstrap import (
    CellBootstrap,
    CellReadyInfo,
    QueenEndpoint,
    cell_endpoint,
    mint_cell_bootstrap,
)
from hivemind.hive.cell_state import VirtualCellStatus
from hivemind.hive.errors import BackendCapabilityError, CellDestroyError, CellProvisionError
from hivemind.hive.models import NetworkPolicy, VirtualCellSpec
from waggle.clock import Clock
from waggle.ids import CellId, HiveId, new_cell_id
from waggle.messages import OsFamily as WireOsFamily

_FAKE_BACKEND_NAME = "fake"  # The `name` and every Cell's `source` this backend hands out.
_FAKE_ARCH = "x86_64"  # images/ is Ubuntu-based and this fake never runs on real hardware.
_FAKE_DISTRIBUTION = "Ubuntu 24.04 LTS"  # Matches codingrules 2's Virtual Cell image baseline.
_FAKE_SHELL = "/bin/bash"
_FAKE_PACKAGE_MANAGER = "apt"
_FAKE_PYTHON_VERSION = "3.12"
_FAKE_CORES = 2  # A modest default host: enough for provision()'s own defaults, no more.
_FAKE_MEMORY_BYTES = 2 * 1024**3
_FAKE_DISK_BYTES = 10 * 1024**3
_FAKE_MAX_SUB_BEES = 4

__all__ = ["FakeCellBackend", "FakeReadinessGate", "ReadinessGateExpect"]


class ReadinessGateExpect(Protocol):
    """The one `hivemind.hive.backends.bootstrap.ReadinessGate` method `FakeCellBackend` needs.

    A narrower structural Protocol than the full `ReadinessGate` (which also carries
    `wait_ready`/`forget`, both a *caller* of `provision()` uses, never the backend itself):
    `FakeCellBackend.__init__`'s own `gate` argument only ever calls `expect`, mirroring exactly
    what a real backend (`hivemind.hive.backends.docker`/`.qemu`) does with its own injected gate
    before starting a Cell's infra (ADR-0027). A real `hivemind.queen.cell_gate.gate.
    QueenReadinessGate` and the in-memory `FakeReadinessGate` below both already satisfy this
    structurally, with no inheritance needed.
    """

    async def expect(self, cell_id: CellId, verify_key_hex: str) -> None:
        """Register `cell_id`'s public key; see `ReadinessGate.expect`."""
        ...


@dataclass(slots=True)
class _TrackedCell:
    """This backend's own bookkeeping for one provisioned Cell; never exposed outside this file."""

    cell: Cell
    status: VirtualCellStatus
    labels: dict[str, str]
    created_at: datetime
    egress_cut: bool = False  # Roadmap step 10.6a: only the control link is reachable while set.


class FakeCellBackend:
    """An in-memory CellBackend: provisions, destroys, pauses and lists Cells with no real infra."""

    def __init__(
        self,
        clock: Clock,
        capabilities: BackendCapabilities | None = None,
        *,
        endpoint: QueenEndpoint | Callable[[], QueenEndpoint | None] | None = None,
        gate: ReadinessGateExpect | None = None,
    ) -> None:
        """Create a FakeCellBackend with nothing provisioned yet.

        Args:
            clock: Source of every minted CellId and every recorded timestamp.
            capabilities: What this fake declares it can do; defaults to can_snapshot=False,
                can_pause=True, headroom=None (unbounded) and can_cut_egress=True, so most tests
                need not think about it.
            endpoint: When given, `provision()` also mints a real `CellBootstrap` for every Cell
                it builds (module docstring's own e2e slice addition); `None` (the default) skips
                that entirely, matching every pre-existing caller's own behaviour. May be a plain
                `QueenEndpoint`, or a zero-argument callable returning one (or `None`), resolved
                fresh on every `provision()` call rather than once here: `hivemind.hive.lifecycle.
                CellLifecycle.reconcile` (called by `hivemind.cli.compose.hive.run_hive` before its
                own `CellListener.start()`) already forces this backend to be constructed, through
                `hivemind.hive.registry.BackendRegistry.get`'s own construct-once-and-cache
                contract, before a real listener's own URL is known -- a plain, eagerly-resolved
                `QueenEndpoint` would bake in "not started yet" forever for this cached instance.
                `hivemind.cli.compose.virtual_cells._build_registry` passes a callable for exactly
                this reason.
            gate: When given (together with `endpoint`), `provision()` also calls `gate.expect()`
                with the freshly minted Cell's own id and public key, exactly as a real backend
                (`hivemind.hive.backends.docker`/`.qemu`) already does through its own injected
                `hivemind.hive.backends.bootstrap.ReadinessGate` (ADR-0027: "expect() before the
                Cell's own infra exists") -- without this, a real `hivemind.queen.cell_gate.gate.
                QueenReadinessGate` never learns this Cell's key, so its own `wait_ready()` (what
                `hivemind.queen.cell_gate.provider.LifecycleVirtualCellProvider` blocks on) fails
                instantly with "never registered" the moment anything real calls it. `None` (the
                default) skips this entirely, matching every pre-existing caller's own behaviour;
                unused whenever `endpoint` resolves to `None` (no bootstrap is ever minted then).
        """
        self._clock = clock
        self._capabilities = (
            capabilities
            if capabilities is not None
            else BackendCapabilities(
                can_snapshot=False, can_pause=True, headroom=None, can_cut_egress=True
            )
        )
        self._endpoint = endpoint
        self._gate = gate
        self._cells: dict[CellId, _TrackedCell] = {}
        self._provision_failure_reason: str | None = None
        self._destroy_failure_reason: str | None = None
        self._provision_delay_s = 0.0
        self._reset_recorded_calls()

    def _reset_recorded_calls(self) -> None:
        """Start (or clear) the per-method call records tests assert on."""
        self.provision_calls: list[VirtualCellSpec] = []
        self.destroy_calls: list[CellId] = []
        self.pause_calls: list[CellId] = []
        self.resume_calls: list[CellId] = []
        self.egress_calls: list[tuple[str, CellId]] = []  # ("cut" | "restore", cell) in order.
        self.bootstraps: dict[CellId, CellBootstrap] = {}

    @property
    def name(self) -> str:
        """This backend's registry name, "fake"."""
        return _FAKE_BACKEND_NAME

    @property
    def capabilities(self) -> BackendCapabilities:
        """What this fake declares it can do; see `__init__`."""
        return self._capabilities

    def set_provision_failure(self, reason: str | None) -> None:
        """Make every subsequent provision() raise CellProvisionError, or stop doing so.

        Args:
            reason: The failure reason every subsequent provision() raises with; None turns the
                switch off and lets provision() succeed again.
        """
        self._provision_failure_reason = reason

    def set_destroy_failure(self, reason: str | None) -> None:
        """Make every subsequent destroy() raise CellDestroyError, or stop doing so.

        Args:
            reason: The failure reason every subsequent destroy() raises with; None turns the
                switch off.
        """
        self._destroy_failure_reason = reason

    def set_provision_delay(self, delay_s: float) -> None:
        """Make provision() await `clock.sleep(delay_s)` before returning; 0 for no delay.

        A test drives this with a FakeClock: a `delay_s` above the spec's own `ready_timeout_s`
        simulates a Cell that never became reachable in time, without a real backend or wait.

        Args:
            delay_s: Seconds to sleep before provision() finishes; must be >= 0.

        Raises:
            ValueError: `delay_s` is negative.
        """
        if delay_s < 0:
            raise ValueError(f"delay_s must be >= 0, got {delay_s}")
        self._provision_delay_s = delay_s

    async def provision(self, spec: VirtualCellSpec) -> Cell:
        """Build and record a Cell from `spec`; see `CellBackend.provision`."""
        self.provision_calls.append(spec)
        # A slow-readiness switch is a real await, driven by the injected clock, so a FakeClock
        # test controls exactly when provision() resumes instead of a real timer.
        if self._provision_delay_s > 0:
            await self._clock.sleep(self._provision_delay_s)
        if self._provision_delay_s > spec.ready_timeout_s:
            # The simulated Cell never became reachable within its own deadline: the same failure
            # shape a real backend's readiness poll would raise (Appendix A.1's own wording).
            raise CellProvisionError(
                self.name, spec.image, f"did not become reachable within {spec.ready_timeout_s}s"
            )
        if self._provision_failure_reason is not None:
            raise CellProvisionError(self.name, spec.image, self._provision_failure_reason)
        headroom = self._capabilities.headroom
        if headroom is not None and len(self._cells) >= headroom:
            raise CellProvisionError(self.name, spec.image, f"at its headroom of {headroom} cells")
        cell_id = await self._mint_cell_id(spec)
        cell = _build_cell(spec, self.name, cell_id)
        # hive_id first so a caller's own spec.labels can never shadow the id the sweep relies on.
        labels = {**spec.labels, "hive_id": spec.hive_id}
        self._cells[cell.id] = _TrackedCell(
            cell=cell, status=VirtualCellStatus.READY, labels=labels, created_at=self._clock.now()
        )
        return cell

    async def _mint_cell_id(self, spec: VirtualCellSpec) -> CellId:
        """Return a fresh CellId, minting and recording a real CellBootstrap when `endpoint` is set.

        With no `endpoint` (the default), this is just `new_cell_id` -- pre-existing behaviour,
        unchanged. With one, the bootstrap's own `mint_cell_bootstrap`-minted `cell_id` is used
        instead of a second, independent one, so `self.bootstraps[cell.id]` always agrees with the
        Cell this call actually builds (module docstring's own e2e slice addition), and, when a
        `gate` was also given, that gate learns this Cell's own public key before this method
        returns -- see `__init__`'s own `gate` docstring for why that matters. `endpoint`'s own
        callable form (`__init__`'s own docstring) is resolved fresh here, on every call, never
        once at construction time.
        """
        endpoint = self._endpoint() if callable(self._endpoint) else self._endpoint
        if endpoint is None:
            return new_cell_id(self._clock)
        # Roadmap step 10.3a: the same per-tier choice every real backend makes.
        bootstrap = mint_cell_bootstrap(
            spec.hive_id, cell_endpoint(endpoint, spec, self.name), self._clock
        )
        self.bootstraps[bootstrap.cell_id] = bootstrap
        if self._gate is not None:
            await self._gate.expect(bootstrap.cell_id, bootstrap.public_key_hex)
        return bootstrap.cell_id

    async def destroy(self, cell_id: CellId) -> None:
        """Remove `cell_id` from this backend's table; see `CellBackend.destroy` (idempotent)."""
        self.destroy_calls.append(cell_id)
        if cell_id not in self._cells:
            return  # Idempotent: nothing to destroy, matching a real backend's own contract.
        if self._destroy_failure_reason is not None:
            raise CellDestroyError(self.name, cell_id, self._destroy_failure_reason)
        del self._cells[cell_id]

    async def list_cells(self, hive_id: HiveId) -> Sequence[VirtualCellRecord]:
        """Return every tracked Cell whose hive_id label matches; see `CellBackend.list_cells`."""
        return tuple(
            VirtualCellRecord(
                cell_id=tracked.cell.id,
                status=tracked.status,
                image=tracked.cell.name,
                labels=dict(tracked.labels),
                created_at=tracked.created_at,
            )
            for tracked in self._cells.values()
            if tracked.labels.get("hive_id") == hive_id
        )

    async def pause(self, cell_id: CellId) -> None:
        """Mark `cell_id` DORMANT in this backend's table; see `CellBackend.pause`."""
        self.pause_calls.append(cell_id)
        self._require_pause_capability(cell_id)
        tracked = self._cells.get(cell_id)
        if tracked is not None:
            tracked.status = VirtualCellStatus.DORMANT

    async def resume(self, cell_id: CellId) -> None:
        """Mark `cell_id` READY in this backend's table; see `CellBackend.resume`."""
        self.resume_calls.append(cell_id)
        self._require_pause_capability(cell_id)
        tracked = self._cells.get(cell_id)
        if tracked is not None:
            tracked.status = VirtualCellStatus.READY

    async def cut_egress(self, cell_id: CellId) -> None:
        """Mark `cell_id`'s egress cut to its control link alone; see `EgressCutter.cut_egress`."""
        self.egress_calls.append(("cut", cell_id))
        self._require_egress_capability(cell_id)
        tracked = self._cells.get(cell_id)
        if tracked is not None:
            tracked.egress_cut = True

    async def restore_egress(self, cell_id: CellId) -> None:
        """Give `cell_id` its own policy's egress back; see `EgressCutter.restore_egress`."""
        self.egress_calls.append(("restore", cell_id))
        self._require_egress_capability(cell_id)
        tracked = self._cells.get(cell_id)
        if tracked is not None:
            tracked.egress_cut = False

    def egress_is_cut(self, cell_id: CellId) -> bool:
        """Return whether `cell_id`'s egress is cut right now; False for a Cell never tracked.

        Args:
            cell_id: The Cell to look up.

        Returns:
            True while only the Cell's control link is reachable (roadmap step 10.6a).
        """
        tracked = self._cells.get(cell_id)
        return tracked is not None and tracked.egress_cut

    def _require_egress_capability(self, cell_id: CellId) -> None:
        """Raise BackendCapabilityError unless this fake declares can_cut_egress."""
        if not self._capabilities.can_cut_egress:
            raise BackendCapabilityError(self.name, "egress cut", cell_id=cell_id)

    def _require_pause_capability(self, cell_id: CellId) -> None:
        """Raise BackendCapabilityError unless this fake declares can_pause."""
        if not self._capabilities.can_pause:
            raise BackendCapabilityError(self.name, "pause", cell_id=cell_id)


def _build_cell(spec: VirtualCellSpec, source: str, cell_id: CellId) -> Cell:
    """Build the VIRTUAL, FULL-access Cell a provisioned `spec` reports.

    A desktop-capable image (spec.exoskeleton) reports has_display/has_audio/has_browser and the
    ability to start a display; a terminal-only image reports none of those, matching
    images/base-ubuntu versus images/desktop-ubuntu (codingrules section 3).
    """
    capabilities = CellCapabilities(
        os=OsFamily.LINUX,
        arch=_FAKE_ARCH,
        distribution=_FAKE_DISTRIBUTION,
        shell=_FAKE_SHELL,
        package_manager=_FAKE_PACKAGE_MANAGER,
        python_version=_FAKE_PYTHON_VERSION,
        has_display=spec.exoskeleton,
        has_audio=spec.exoskeleton,
        has_browser=spec.exoskeleton,
        can_start_display=spec.exoskeleton,
        can_host_model=True,
        network_scopes=(
            spec.network_allowlist if spec.network_policy is NetworkPolicy.ALLOWLIST else ()
        ),
    )
    return Cell(
        id=cell_id,
        kind=CellKind.VIRTUAL,
        name=spec.image,
        source=source,
        capabilities=capabilities,
        capacity=spec.capacity,
        access_level=AccessLevel.FULL,
        comb_shield=spec.comb_shield,
    )


# What wait_ready() returns for a Cell no test arranged with set_ready_info: a modest,
# terminal-only Cell matching _build_cell's own defaults above, so a test that never calls
# set_ready_info still gets a self-consistent, validator-passing CellReadyInfo.
_DEFAULT_READY_INFO = CellReadyInfo(
    capabilities=CellCapabilities(
        os=OsFamily.LINUX,
        arch=_FAKE_ARCH,
        distribution=_FAKE_DISTRIBUTION,
        shell=_FAKE_SHELL,
        package_manager=_FAKE_PACKAGE_MANAGER,
        python_version=_FAKE_PYTHON_VERSION,
        has_display=False,
        has_audio=False,
        has_browser=False,
        can_start_display=False,
        can_host_model=True,
        network_scopes=(),
    ),
    capacity=ForageCapacity(
        host=HostCapacity(
            cores=_FAKE_CORES,
            memory_bytes=_FAKE_MEMORY_BYTES,
            memory_free_bytes=_FAKE_MEMORY_BYTES,
            disk_bytes=_FAKE_DISK_BYTES,
            disk_free_bytes=_FAKE_DISK_BYTES,
            cpu_load=0.0,
            gpus=(),
            arch=_FAKE_ARCH,
            os=WireOsFamily.LINUX,
        ),
        local_seats=(),
        max_sub_bees=_FAKE_MAX_SUB_BEES,
    ),
)


class FakeReadinessGate:
    """An in-memory ReadinessGate: ready immediately, unless armed never-ready for a Cell.

    Mirrors `FakeCellBackend`'s own shape: an injected Clock for deterministic timing, one switch
    per simulated failure mode, and every call recorded so a test can assert on what was actually
    asked for.
    """

    def __init__(self, clock: Clock) -> None:
        """Create a FakeReadinessGate with nothing expected or armed yet.

        Args:
            clock: Source of the real wait a `set_never_ready` Cell's `wait_ready` performs before
                raising, so a test drives it with a FakeClock instead of a real timer.
        """
        self._clock = clock
        self._never_ready: set[CellId] = set()
        self._never_ready_next = False
        self._ready_info: dict[CellId, CellReadyInfo] = {}
        self.expect_calls: list[CellId] = []
        self.forget_calls: list[CellId] = []

    def set_ready_info(self, cell_id: CellId, info: CellReadyInfo) -> None:
        """Make `wait_ready(cell_id, ...)` return `info` instead of the module's own default.

        Args:
            cell_id: Which Cell's result to override.
            info: The CellReadyInfo `wait_ready` should return for it.
        """
        self._ready_info[cell_id] = info

    def set_never_ready(self, cell_id: CellId, never_ready: bool = True) -> None:
        """Make `wait_ready(cell_id, ...)` wait out `timeout_s` and raise, or stop doing so.

        Args:
            cell_id: Which Cell's wait to arm or disarm.
            never_ready: True arms the switch; False disarms it, letting `wait_ready` succeed
                again.
        """
        if never_ready:
            self._never_ready.add(cell_id)
        else:
            self._never_ready.discard(cell_id)

    def set_next_never_ready(self) -> None:
        """Arm never-ready for whichever Cell the very next `expect()` call registers.

        A caller (a `CellBackend.provision()` under test) usually mints its own `CellId`
        internally, so a test cannot name it in advance the way `set_never_ready` needs; this is
        the one-shot alternative for exactly that case.
        """
        self._never_ready_next = True

    async def expect(self, cell_id: CellId, verify_key_hex: str) -> None:
        """Record the expectation; see `ReadinessGate.expect`."""
        self.expect_calls.append(cell_id)
        if self._never_ready_next:
            self._never_ready.add(cell_id)
            self._never_ready_next = False

    async def wait_ready(self, cell_id: CellId, timeout_s: float) -> CellReadyInfo:
        """Return this Cell's arranged or default CellReadyInfo; see `ReadinessGate.wait_ready`."""
        if cell_id in self._never_ready:
            # A real gate's own deadline is what a caller's timeout_s bounds; the fake clock makes
            # this instant in a test while still exercising the exact same timeout code path.
            await self._clock.sleep(timeout_s)
            raise TimeoutError(
                f"Cell {cell_id} never reported ready within {timeout_s}s (FakeReadinessGate)."
            )
        return self._ready_info.get(cell_id, _DEFAULT_READY_INFO)

    async def forget(self, cell_id: CellId) -> None:
        """Drop any arranged state for `cell_id`; see `ReadinessGate.forget` (idempotent)."""
        self.forget_calls.append(cell_id)
        self._never_ready.discard(cell_id)
        self._ready_info.pop(cell_id, None)
