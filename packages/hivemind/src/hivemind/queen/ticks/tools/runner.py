"""Run the Quarantine Comb for the Queen: keep the package, then prove it where the sandbox is.

`QueenSandboxRunner` is the promotion flow's `SandboxRunner` (ADR-0047). It first keeps the
package in the Basket, through the one deposit door, so the bytes have a hash, a signature and a
Honey record. Then it picks the sandbox the manifest names. With `[royal_jelly] sandbox =
"subprocess"` (development only) the Comb runs right here, in this job, over a temporary directory;
it is already off the Queen's tick. With the default `virtual` sandbox the runner provisions a
disposable Virtual Cell with no network, ships the package to it as a Basket blob, orders that
Cell's own Warden to run the Comb with a `tool.quarantine` and awaits the `tool.quarantine_report`
the Warden sends back. The report arrives in a later tick, which resolves a future this job waits
on; the sandbox is released in a `finally`, so a failed, timed-out or cancelled run never leaves
one behind. Every run's spec and report are kept (bounded), because a Warden that later promotes a
cell-scope tool is held to the report the Queen holds, not to its own word.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside `queen.ticks.tools`. Built
    by the request and quarantine handlers for each job; calls into `hivemind.royal_jelly` (the
    Comb, the subprocess sandbox, the report), `hivemind.basket` (the deposit door),
    `hivemind.hive` (the sandbox's Cell spec), `.transfer` (shipping) and waggle.

Key invariants:
    - A sandbox Cell has `NetworkPolicy.NONE` and the MEADOW tier, and is torn down in a `finally`
      that also runs on cancellation; the provision itself is finished (never cut short) before a
      cancellation is honoured, so no Cell is orphaned.
    - The Queen sends the order only after the package is resident on the sandbox's Cell.
    - A report is accepted only if it proves this tool's spec and this package's bytes.
    - An unreachable sandbox is `SandboxUnavailableError`, never a failing report: it is no
      verdict on the tool.

See Also:
    - docs/adr/0047-quarantine-comb-gates-and-sandbox.md for why the Comb runs where its sandbox is.
    - hivemind.royal_jelly.promotion.ports for the `SandboxRunner` protocol this satisfies.
"""

from __future__ import annotations

import asyncio
import contextlib

from pydantic import ValidationError

from hivemind.basket import BasketError, BlobNotFoundError, DepositRequest, MediaKind
from hivemind.cell import CombShieldLevel, HoneyClearance, OsFamily, hive_stand_cell_id
from hivemind.common.errors import HiveMindError
from hivemind.common.logging import get_logger
from hivemind.hive import NetworkPolicy, VirtualCellSpec
from hivemind.hive.models import CellReservation
from hivemind.manifest import RoyalJellySection, SandboxKind
from hivemind.queen.deps import (
    PendingReport,
    QuarantineRecord,
    QueenDeps,
    SandboxCellProvider,
    WardenLink,
)
from hivemind.queen.errors import SandboxUnavailableError
from hivemind.queen.ticks.tools.events import record_tool
from hivemind.queen.ticks.tools.transfer import ship_blob
from hivemind.royal_jelly import RoyalJellyAccess
from hivemind.royal_jelly.quarantine_comb import QuarantineComb, SandboxSubprocess
from hivemind.royal_jelly.spec import QuarantineReport, ToolSpec, sha256_of, spec_sha256
from waggle.envelope import wrap
from waggle.ids import CellId, MessageId
from waggle.messages.labels import OsFamily as WireOsFamily
from waggle.messages.tool import QuarantinePath, ToolQuarantine, ToolQuarantineReport

SANDBOX_PURPOSE_LABEL = "hivemind.purpose"  # The label every backend stamps; the sweep reads it.
SANDBOX_PURPOSE = "comb-sandbox"
SANDBOX_ARCH = "x86_64"  # images/ is Ubuntu-based (codingrules section 2), as every Virtual Cell.
SANDBOX_MAX_SUB_BEES = 1  # A sandbox runs one Comb: the sandbox Warden spawns no sub-bee.
MAX_RECORDS = 256  # How many runs' specs and reports the Queen keeps; the oldest are dropped.
ZIP_MEDIA_TYPE = "application/zip"  # A tool package is a deterministic zip archive.

log = get_logger(__name__)

__all__ = ["QueenSandboxRunner", "keep_record", "sandbox_cell_spec"]


def sandbox_cell_spec(deps: QueenDeps, config: RoyalJellySection) -> VirtualCellSpec:
    """Build the spec of a sandbox Cell from `[royal_jelly]`: no network, the baseline tier.

    Args:
        deps: The Queen's collaborators; the Hive id labels the Cell for the orphan sweep.
        config: `[royal_jelly]`: the image and the Cell's footprint and lifetime.

    Returns:
        A validated spec with `NetworkPolicy.NONE`, MEADOW and the `comb-sandbox` purpose label.
    """
    reservation = CellReservation(
        cpu_cores=config.sandbox_cpu_cores,
        memory_bytes=config.sandbox_memory_bytes,
        disk_bytes=config.sandbox_disk_bytes,
        max_sub_bees=SANDBOX_MAX_SUB_BEES,
    )
    return VirtualCellSpec(
        image=config.sandbox_image,
        cpu_cores=config.sandbox_cpu_cores,
        memory_bytes=config.sandbox_memory_bytes,
        disk_bytes=config.sandbox_disk_bytes,
        lifetime_s=max(1, int(config.sandbox_lifetime_s)),
        network_policy=NetworkPolicy.NONE,
        capacity=reservation.capacity(arch=SANDBOX_ARCH, os=WireOsFamily.LINUX),
        comb_shield=CombShieldLevel.MEADOW,
        hive_id=deps.identity.hive_id,
        labels={SANDBOX_PURPOSE_LABEL: SANDBOX_PURPOSE},
    )


def keep_record(deps: QueenDeps, record: QuarantineRecord) -> None:
    """Remember a run's spec and report by (tool id, version); the oldest past the bound go."""
    records = deps.tool_lane.records
    records[(record.spec.tool_id, record.spec.version)] = record
    while len(records) > MAX_RECORDS:
        del records[next(iter(records))]  # A dict keeps insertion order: the first key is oldest.


class QueenSandboxRunner:
    """Prove a package in a sandbox and bring back the Comb's report (`SandboxRunner`)."""

    def __init__(self, deps: QueenDeps) -> None:
        """Bind the runner to the Queen's collaborators.

        Args:
            deps: The Queen's collaborators; `royal_jelly`, `sandboxes` and `tool_lane` are used.
        """
        self._deps = deps

    async def quarantine(
        self, spec: ToolSpec, package: bytes, path: QuarantinePath
    ) -> QuarantineReport:
        """Keep `package`, run the Comb on it in the configured sandbox and return the report.

        Args:
            spec: The spec the package claims to satisfy.
            package: The package archive bytes (untrusted).
            path: The route the Comb is to record (`SANDBOX_CELL` or `REAL_CELL`).

        Returns:
            The report, passed or failed.

        Raises:
            SandboxUnavailableError: No sandbox could be provisioned or reached, the package could
                not be kept, or the sandbox's report did not prove this tool.
        """
        access = self._deps.royal_jelly
        if access is None:
            raise SandboxUnavailableError("no Royal Jelly Lab is wired to this Hive.")
        digest = await self._keep(access, spec, package)
        if access.sandbox_kind is SandboxKind.SUBPROCESS:
            report = await self._in_process(access, spec, package, path)
        else:
            report = await self._in_cell(access, spec, (digest, len(package), path))
        keep_record(
            self._deps,
            QuarantineRecord(spec, report, digest, spec.requested_by, spec.cell_id),
        )
        return report

    async def _keep(self, access: RoyalJellyAccess, spec: ToolSpec, package: bytes) -> str:
        """Deposit the package (unless the Basket already holds it) and return its hash."""
        digest = sha256_of(package)
        with contextlib.suppress(BlobNotFoundError):
            await access.basket.get(digest)
            return digest  # Already kept: a Warden shipped it up, or this exact package came back.
        stand = hive_stand_cell_id(self._deps.identity.node_id)
        request = DepositRequest(
            content=package,
            title=f"Tool {spec.name} {spec.version} package",
            summary=(
                f"The package of the tool {spec.name} {spec.version} at {spec.scope.value} scope, "
                f"requested by Warden {spec.requested_by}; it runs only once the Comb Registry "
                "promotes it."
            ),
            media_kind=MediaKind.TOOL_PACKAGE,
            media_type=ZIP_MEDIA_TYPE,
            platform_tags=tuple(OsFamily(os.value) for os in spec.supported_os),
            declared=HoneyClearance.C0,
            origin_cell_id=stand,
            # The Queen is no Worker or Warden id, which is all a blob's origin bee may name.
            origin_bee=None,
            # The Hive Stand is the operator's own machine, so the Basket's floor labels it C2.
            from_borrowed_cell=True,
            tier=CombShieldLevel.MEADOW,
        )
        try:
            outcome = await access.deposits.deposit(request)
        except BasketError as exc:
            for event in exc.events:
                await self._deps.trail.record(event)
            raise SandboxUnavailableError(
                f"the package of tool {spec.name} could not be kept in the Basket: {exc.code}."
            ) from exc
        for event in outcome.events:
            await self._deps.trail.record(event)
        return outcome.blob.sha256

    async def _in_process(
        self, access: RoyalJellyAccess, spec: ToolSpec, package: bytes, path: QuarantinePath
    ) -> QuarantineReport:
        """Run the Comb over a temporary directory here (development only; already off the tick)."""
        clock = self._deps.clock
        sandbox = SandboxSubprocess(clock, output_bytes=access.limits.output_bytes)
        # External await: six gates in a child process; each is bounded by the limits' wall time.
        return await QuarantineComb(clock).run(package, spec, sandbox, access.limits, path)

    async def _in_cell(
        self,
        access: RoyalJellyAccess,
        spec: ToolSpec,
        proof: tuple[str, int, QuarantinePath],
    ) -> QuarantineReport:
        """Provision a sandbox Cell, run the Comb there through its Warden, and release the Cell."""
        provider = self._deps.sandboxes
        if provider is None:
            raise SandboxUnavailableError(
                "no Virtual backend is configured ([virtual_cells] backend), so the sandbox "
                "Cell cannot be provisioned."
            )
        link = await self._acquire(provider, sandbox_cell_spec(self._deps, access.config))
        lane = self._deps.tool_lane
        lane.sandbox_cells.add(link.cell.id)
        try:
            await record_tool(
                self._deps,
                "tool.sandbox_provisioned",
                spec.tool_id,
                sandbox_cell_id=link.cell.id,
                image=access.config.sandbox_image,
            )
            return await self._prove(access, link, spec, proof)
        finally:
            lane.sandbox_cells.discard(link.cell.id)
            await _release(self._deps, provider, spec, link.cell.id)

    async def _acquire(
        self, provider: SandboxCellProvider, sandbox_spec: VirtualCellSpec
    ) -> WardenLink:
        """Provision the sandbox; a cancellation lets the provision finish and releases its Cell."""
        acquiring = asyncio.ensure_future(provider.acquire_sandbox(sandbox_spec))
        try:
            # Shielded: a provision cut short could leave a container its backend never cleans up.
            return await asyncio.shield(acquiring)
        except asyncio.CancelledError:
            with contextlib.suppress(Exception):
                link = await acquiring
                await provider.release_sandbox(link.cell.id)
            raise
        except (HiveMindError, OSError, TimeoutError) as exc:
            raise SandboxUnavailableError(f"no sandbox Cell could be provisioned: {exc}") from exc

    async def _prove(
        self,
        access: RoyalJellyAccess,
        link: WardenLink,
        spec: ToolSpec,
        proof: tuple[str, int, QuarantinePath],
    ) -> QuarantineReport:
        """Ship the package to the sandbox's Warden, order the Comb and read its report."""
        digest, size, path = proof
        try:
            async with asyncio.timeout(access.config.request_timeout_s):
                if not await ship_blob(self._deps, link, digest, f"quarantine of {spec.name}"):
                    raise SandboxUnavailableError(
                        f"the package of tool {spec.name} could not be shipped to the sandbox Cell."
                    )
                reply = await self._order(access, link, spec, (digest, size, path))
        except TimeoutError as exc:
            raise SandboxUnavailableError(
                f"the sandbox Cell's Warden did not report on tool {spec.name} in time."
            ) from exc
        return _checked(reply, spec, digest)

    async def _order(
        self,
        access: RoyalJellyAccess,
        link: WardenLink,
        spec: ToolSpec,
        proof: tuple[str, int, QuarantinePath],
    ) -> ToolQuarantineReport:
        """Send `tool.quarantine` and await the report a later tick resolves."""
        digest, size, path = proof
        try:
            order = ToolQuarantine(
                tool_id=spec.tool_id,
                name=spec.name,
                version=spec.version,
                scope=spec.scope,
                cell_id=spec.cell_id,
                spec_json=spec.to_json(),
                package_sha256=digest,
                package_size_bytes=size,
                path=path,
                limits_json=access.limits.to_json(),
                requested_by_warden=spec.requested_by,
                reason=f"Quarantine run for tool {spec.name} {spec.version}.",
            )
        except ValidationError as exc:
            raise SandboxUnavailableError(f"the order for tool {spec.name} is malformed.") from exc
        envelope = wrap(order, link.hop, clock=self._deps.clock)
        key = MessageId(envelope.id)
        pending = self._deps.tool_lane.pending_reports
        future: asyncio.Future[ToolQuarantineReport] = asyncio.get_running_loop().create_future()
        pending[key] = PendingReport(link.warden_id, future)
        try:
            if not await link.send(envelope):
                raise SandboxUnavailableError("the sandbox Cell's Warden could not be reached.")
            # External await: the sandbox Warden runs six gates; its report resolves this future
            # in a later tick, and the caller's timeout bounds the wait.
            return await future
        finally:
            pending.pop(key, None)


async def _release(
    deps: QueenDeps, provider: SandboxCellProvider, spec: ToolSpec, cell: CellId
) -> None:
    """Tear the sandbox down and record it; a failed teardown is logged, never raised."""
    try:
        await provider.release_sandbox(cell)
    except (HiveMindError, OSError) as exc:
        log.warning("queen.tools.sandbox_release_failed", cell_id=cell, error=repr(exc))
    await record_tool(deps, "tool.sandbox_released", spec.tool_id, sandbox_cell_id=cell)


def _checked(reply: ToolQuarantineReport, spec: ToolSpec, digest: str) -> QuarantineReport:
    """Parse the sandbox Warden's report and require that it proves this spec and this package."""
    try:
        report = QuarantineReport.from_json(reply.report_json)
    except (ValidationError, ValueError) as exc:
        raise SandboxUnavailableError(
            f"the sandbox's report on tool {spec.name} is not readable."
        ) from exc
    proves_this = (
        report.tool_id == spec.tool_id
        and report.package_sha256 == digest
        and report.spec_sha256 == spec_sha256(spec)
    )
    if not proves_this:
        raise SandboxUnavailableError(
            f"the sandbox's report does not prove tool {spec.name} {spec.version} as packaged."
        )
    return report
