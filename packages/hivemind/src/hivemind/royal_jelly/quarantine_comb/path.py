"""Decide which route through the Quarantine Comb proves a tool for a given target platform.

The Comb's sandbox is an Ubuntu Virtual Cell, so a tool written for Windows, macOS or another
Linux distribution cannot be fully exercised in it. ADR-0047 therefore gives the Comb two
routes: the sandbox route (every gate runs in the sandbox) when the target is the sandbox
image's own platform, and the real-cell route (static, schema and fuzz gates in the sandbox, the
tests gate on a Real Cell of the target platform) for everything else. The decision is a pure
function of the target's platform facts and the sandbox's own platform (the image's distribution
name, and its OS family: Linux for an image, this host's for the development subprocess sandbox,
which is the Hive Stand itself), so it is decided once, here, and recorded on the report.

Fits into the Hive:
    Layer 3 (sources of Cells, and capabilities handed down), inside royal_jelly.quarantine_comb.
    Called by the promotion flow before it asks for a sandbox, and by the Queen when a request
    names a target platform. Calls into waggle's platform report and labels only.

Key invariants:
    - SANDBOX_CELL exactly when the target is the sandbox's own platform: for a Linux sandbox,
      Linux of the image's distribution (a Linux with no distribution label is REAL_CELL); for a
      Windows or macOS one (only ever the development subprocess), the same family.
    - Pure: no I/O, no clock.

See Also:
    - docs/adr/0047 (the Comb's gates and sandbox) for the two routes and their reasons.
    - .claude/roadmap.md step 9.3a for the non-Ubuntu target rule.
"""

from __future__ import annotations

from waggle.messages.labels import OsFamily
from waggle.messages.reports import PlatformReport
from waggle.messages.tool.authoring import QuarantinePath

__all__ = ["PATH_RATIONALE", "decide_path"]

# One plain sentence per route, for the trail and the CLI: why the Comb took it (ADR-0047).
PATH_RATIONALE = {
    QuarantinePath.SANDBOX_CELL: (
        "The target is the sandbox image's own platform, so every gate runs in the sandbox."
    ),
    QuarantinePath.REAL_CELL: (
        "The sandbox cannot imitate the target platform: static, schema and fuzz gates run in "
        "the sandbox and the tests gate runs on a Real Cell of the target platform."
    ),
}


def decide_path(
    platform: PlatformReport, sandbox_distribution: str, sandbox_os: OsFamily = OsFamily.LINUX
) -> QuarantinePath:
    """Choose the sandbox route or the real-cell route for a target platform.

    Args:
        platform: What the tool's target Cell runs (OS family and distribution label).
        sandbox_distribution: The sandbox image's distribution name, from the manifest
            (`[royal_jelly] sandbox_distribution`, default `ubuntu`). Compared case-insensitively.
        sandbox_os: The sandbox's OS family: Linux for every image, and this host's for the
            development subprocess sandbox, which runs on the Hive Stand itself.

    Returns:
        `SANDBOX_CELL` when `platform` is the sandbox's own (Linux of that distribution, or the
        same non-Linux family), otherwise `REAL_CELL`.
    """
    # Another family is a different kernel and userland from the sandbox's.
    if platform.os is not sandbox_os:
        return QuarantinePath.REAL_CELL
    # Windows and macOS carry no distribution: the family is the whole platform there.
    if sandbox_os is not OsFamily.LINUX:
        return QuarantinePath.SANDBOX_CELL
    # A Linux with no label cannot be shown to match, and an unproven match is not a match.
    label = (platform.distribution or "").strip().lower()
    wanted = sandbox_distribution.strip().lower()
    if not label or not wanted:
        return QuarantinePath.REAL_CELL
    # The probe reports os-release's PRETTY_NAME ("Ubuntu 24.04.1 LTS"), the manifest the bare
    # name ("ubuntu"); so the name matches as a leading word, not only as the whole label.
    if label == wanted or label.startswith(f"{wanted} "):
        return QuarantinePath.SANDBOX_CELL
    return QuarantinePath.REAL_CELL
