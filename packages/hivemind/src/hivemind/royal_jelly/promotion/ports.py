"""Define the promotion flow's three outward seams: the sandbox runner, Honey and the announcer.

`PromotionFlow` decides what happens next in a tool's life but does none of the effectful work
itself. Running the Quarantine Comb is somebody else's job (the Queen's broker ships the package
to a sandbox Cell and orders its Warden to run the Comb, or a local in-process Comb runs it
directly), describing a promotion to the Hive's knowledge base is Honey's job, and telling the
Wardens whose Cells can use the tool is the Queen's. Each is a Protocol here so the flow is
testable with fakes and the composition root plugs in the real thing.

Fits into the Hive:
    Layer 3 (sources of Cells, and capabilities handed down), inside royal_jelly.promotion.
    Satisfied by the Queen's tool job (runner, announcer) and the composition root's Honey adapter
    (`hivemind.royal_jelly.honey_link` builds what that adapter submits); called only by
    `PromotionFlow`.

Key invariants:
    - `SandboxRunner.quarantine` returns a report, passed or failed; it raises only when the
      sandbox itself could not be reached or built, which the flow records as a rejection.
    - `HoneyDeposit` and `Announcer` run after the registry has promoted: a failure in either never
      undoes a promotion (the flow logs it and carries on).

See Also:
    - docs/adr/0047 (the Quarantine Comb's gates and sandbox) for what a runner does.
    - hivemind.royal_jelly.honey_link for the Nectar a Honey deposit carries.
"""

from __future__ import annotations

from typing import Protocol

from hivemind.royal_jelly.registry import RegistryChange
from hivemind.royal_jelly.spec import QuarantineReport, ToolSpec
from waggle.messages.tool.authoring import QuarantinePath

__all__ = ["Announcer", "HoneyDeposit", "SandboxRunner"]


class SandboxRunner(Protocol):
    """Run the Quarantine Comb on a package somewhere safe and bring back its report."""

    async def quarantine(
        self, spec: ToolSpec, package: bytes, path: QuarantinePath
    ) -> QuarantineReport:
        """Prove `package` against `spec` in a sandbox and return the Comb's report.

        Args:
            spec: The spec the package claims to satisfy.
            package: The package bytes (the archive whose SHA-256 names the version).
            path: Which route to take: the sandbox Cell alone, or the real-Cell tests leg.

        Returns:
            The report; `passed` False when a gate failed. Never raises for a failing tool.

        Raises:
            RoyalJellyError: The sandbox could not be provisioned or reached, so no verdict
                exists.
        """
        ...


class HoneyDeposit(Protocol):
    """Describe a promoted tool to the Honey Store so a bee can find it (roadmap 9.8)."""

    async def deposit_promotion(
        self, spec: ToolSpec, report: QuarantineReport, package_sha256: str
    ) -> None:
        """Deposit the Nectar describing a promoted tool.

        Args:
            spec: The promoted spec.
            report: The passing report that proved it.
            package_sha256: The hash of the promoted package.
        """
        ...


class Announcer(Protocol):
    """Tell the Wardens whose Cells can use a tool that a version was promoted."""

    async def announce(self, change: RegistryChange, spec: ToolSpec) -> None:
        """Announce a promotion (a `tool.promoted` to each Warden whose OS the tool supports).

        Args:
            change: What the registry did; `change.row` is the new current row.
            spec: The promoted spec.
        """
        ...
