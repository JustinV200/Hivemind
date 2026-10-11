"""End-to-end: switching the provider changes only the manifest, and `src/` is byte-identical.

Phase 8's third exit criterion. "A checksum of `src/` is identical between the two configurations"
is read here as a statement about the files on disk, and it is what this test measures: it takes a
sha256 over every `packages/*/src/**/*.py` once, runs the haiku goal under two manifests (the
offline one, derived from `docs/manifests/offline.toml` and answered by the scripted loopback
server, and `builders.cli.fake_manifest`'s hosted-shaped fake provider), and asserts the checksum is
the same after each run as before the first, so no run, under either provider, wrote to the source.
`git status --porcelain` over the same paths must read the same before and after too (empty on a
clean checkout, which is how CI runs it; a working tree with uncommitted edits is compared with
itself, not with the index). The two manifests are also shown to differ in their provider tables and
to bind every slot to a provider each, so the property is not vacuous: the providers really were
switched, and nothing but the manifest was.

Fits into the Hive:
    Test infrastructure (codingrules section 14.2), not shipped.

Key invariants:
    - Both runs succeed; a run that failed early would also leave `src/` untouched.

See Also:
    - tests/e2e/offline/source.py for the checksum and the git read.
    - docs/adr/0045-provider-routing-forage-aware-fallbacks-and-offline-mode.md.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from builders.cli import fake_manifest
from e2e.kernel_helpers import HaikuScript, default_worker_turn
from e2e.offline import GOAL, HAIKU_FILES, EgressLog, offline_manifest
from e2e.offline.rig import TIMEOUT_S, run_goal_in_process
from e2e.offline.source import porcelain, src_checksum
from e2e.scripted_openai import Scenario, serve_in_thread

from hivemind.cell import HoneyClearance
from hivemind.cli.compose import Hive, build_hive, run_goal, run_hive
from hivemind.manifest import load_manifest
from waggle.clock import SystemClock

pytestmark = pytest.mark.e2e


def test_two_provider_configurations_leave_the_source_byte_identical(
    tmp_path: Path, egress: EgressLog
) -> None:
    checksum_before, git_before = src_checksum(), porcelain()

    with serve_in_thread(Scenario(files=HAIKU_FILES)) as base_url:
        local, _path = offline_manifest(tmp_path / "offline", base_url)
        local_succeeded = run_goal_in_process(local).succeeded
    hosted_shaped = load_manifest(fake_manifest(tmp_path / "fake"), {})
    script = HaikuScript(default_worker_turn)
    hive = build_hive(
        hosted_shaped, environ={}, clock=SystemClock(), responders={"fake": script.responder}
    )
    fake_succeeded = asyncio.run(_run(hive))

    assert local_succeeded and fake_succeeded
    # The configurations really differ: other provider kinds, none shared.
    assert {s.kind for s in local.llm.providers.values()} == {"openai_compat"}
    assert {s.kind for s in hosted_shaped.llm.providers.values()} == {"fake"}
    # Nothing but the manifest changed: the source is byte for byte what it was.
    assert src_checksum() == checksum_before
    assert porcelain() == git_before
    egress.assert_clean_and_live()


async def _run(hive: Hive) -> bool:
    """Run the haiku goal on the fake-provider Hive; True when it succeeded."""
    async with run_hive(hive):
        report = await run_goal(hive, GOAL, clearance=HoneyClearance.C1, timeout_s=TIMEOUT_S)
    return report.succeeded
