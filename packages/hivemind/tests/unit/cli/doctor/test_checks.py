"""Test `hive doctor`'s host and configuration checks against starter manifests in temp dirs.

Fits into the Hive:
    Unit tests for `hivemind.cli.doctor.host` and `hivemind.cli.doctor.config` (roadmap 14.2).
    The provider and model checks need a server and are proved in tests/e2e/test_init_doctor.py.

Key invariants:
    - Every manifest is written under the test's own temp directory; Docker is a fake probe.

See Also:
    - hivemind.cli.doctor for what is under test.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import Path

import pytest

from hivemind.cli.doctor import Outcome
from hivemind.cli.doctor.config import check_escalation_policy, check_forage
from hivemind.cli.doctor.host import DockerProbe, check_docker, check_scratch_root
from hivemind.manifest import HiveManifest, load_manifest
from hivemind.manifest.starter import StarterChoice, StarterProvider, render_starter
from waggle.clock import FakeClock

STARTER_SCRATCH = 'scratch_root = ".hive/scratch"'  # The starter's own scratch line.
DOCKER = '\n[virtual_cells]\nbackend = "docker"\n'  # Appended: the Docker backend configured.


def write_manifest(directory: Path, edit: Callable[[str], str] = lambda text: text) -> HiveManifest:
    """Write an edited Ollama-path starter manifest into `directory` and load it back."""
    text = render_starter(StarterChoice(StarterProvider.OLLAMA, model="m1"), FakeClock())
    path = directory / "hive.toml"
    path.write_text(edit(text), encoding="utf-8")
    return load_manifest(path)


def _scratch_is_a_file(directory: Path) -> Callable[[str], str]:
    """Point the scratch root at a plain file, which can never hold a lease's directory."""
    (directory / "a-file").write_text("", encoding="utf-8")
    return lambda text: text.replace(STARTER_SCRATCH, 'scratch_root = "a-file/scratch"')


@pytest.mark.parametrize(
    ("edit", "outcome"),
    [
        (lambda _dir: lambda text: text, Outcome.PASSED),
        (_scratch_is_a_file, Outcome.FAILED),
        (
            lambda _dir: lambda text: text.replace("enabled = true", "enabled = false"),
            Outcome.SKIPPED,
        ),
    ],
)
def test_check_scratch_root_reports_whether_leases_can_use_it(
    tmp_path: Path, edit: Callable[[Path], Callable[[str], str]], outcome: Outcome
) -> None:
    manifest = write_manifest(tmp_path, edit(tmp_path))

    check = check_scratch_root(manifest)

    assert check.outcome is outcome, check.detail


async def _answers() -> None:
    """A Docker daemon that answers."""


async def _unreachable() -> None:
    """A Docker daemon that is not running."""
    raise ConnectionRefusedError("no daemon on the socket")


@pytest.mark.parametrize(
    ("extra", "probe", "outcome"),
    [
        ("", _unreachable, Outcome.SKIPPED),
        (DOCKER, _answers, Outcome.PASSED),
        (DOCKER, _unreachable, Outcome.FAILED),
    ],
)
def test_check_docker_probes_only_a_docker_backend(
    tmp_path: Path, extra: str, probe: DockerProbe, outcome: Outcome
) -> None:
    manifest = write_manifest(tmp_path, lambda text: text + extra)

    check = asyncio.run(check_docker(manifest, probe))

    assert check.outcome is outcome, check.detail


@pytest.mark.parametrize(
    ("policy", "outcome"),
    [
        (None, Outcome.PASSED),
        ("rules = [", Outcome.FAILED),
        ('default = "ESCALATE_TO_HUMAN"\nrules = []\nsurprise = 1\n', Outcome.FAILED),
    ],
)
def test_check_escalation_policy_loads_the_file_a_warden_would(
    tmp_path: Path, policy: str | None, outcome: Outcome
) -> None:
    # None keeps the shipped default; text is written as the operator's own policy file.
    extra = ""
    if policy is not None:
        (tmp_path / "policy.toml").write_text(policy, encoding="utf-8")
        extra = '\n[supervision]\npolicy_file = "policy.toml"\n'
    manifest = write_manifest(tmp_path, lambda text: text + extra)

    check = check_escalation_policy(manifest)

    assert check.outcome is outcome, check.detail


def test_check_forage_warns_on_a_chat_binding_no_map_row_prices(tmp_path: Path) -> None:
    covered = write_manifest(tmp_path)
    # The judge moved to a model with no [forage.map] row: the Hive runs, unpriced.
    judge = '[llm.slots.judge]\nprovider = "local"\nmodel = "m1"'
    uncovered = write_manifest(
        tmp_path, lambda text: text.replace(judge, judge.replace('"m1"', '"m2"'))
    )

    assert check_forage(covered).outcome is Outcome.PASSED
    warned = check_forage(uncovered)
    assert warned.outcome is Outcome.WARNED
    assert "judge (local/m2)" in warned.detail
