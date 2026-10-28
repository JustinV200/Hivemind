"""Prove a newcomer's first two commands: `hive init`, then `hive doctor` against a real server.

Roadmap 14.2. `hive init --provider openai-compatible` writes the starter manifest pointed at the
scripted OpenAI-compatible server on loopback, and `hive doctor` runs every check over real
sockets: the provider answers, every slot's model is on the server (or is not), and with nothing
listening the provider is down. Docker is a fake probe, and the environment is empty, so nothing
leaves the machine and no real Hive is read.

Fits into the Hive:
    End-to-end tests (codingrules 14.2) for `hivemind.cli.init` and `hivemind.cli.doctor`, driven
    through the typer application. Calls into tests/e2e/scripted_openai.py as the model server.

Key invariants:
    - Every file lives under the test's own temp directory.

See Also:
    - tests/unit/cli/doctor/test_checks.py for the host and configuration checks one by one.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest
from e2e.scripted_openai import Scenario, free_port, serve_in_thread
from typer.testing import CliRunner

from hivemind.cli.app import app
from hivemind.cli.doctor import DoctorDeps

pytestmark = pytest.mark.e2e

runner = CliRunner()
SERVED = "scripted-worker"  # A model id the scripted server lists on /models.
INIT = ["init", "--provider", "openai-compatible"]  # The newcomer's first command, less its values.


async def _no_docker_needed() -> None:
    """Stand in for the Docker probe; the starter configures no Docker backend anyway."""


@contextmanager
def _dead_server() -> Iterator[str]:
    """Yield a loopback /v1 URL nothing listens on."""
    yield f"http://127.0.0.1:{free_port()}/v1"


@pytest.mark.parametrize(
    ("model", "live", "expected"),
    [
        (SERVED, True, (0, "PASSED", "PASSED")),
        ("scripted-missing", True, (1, "PASSED", "FAILED")),
        (SERVED, False, (1, "FAILED", "SKIPPED")),
    ],
)
def test_doctor_checks_a_fresh_starter_against_its_server(
    tmp_path: Path, model: str, live: bool, expected: tuple[int, str, str]
) -> None:
    exit_code, provider, models = expected
    server = serve_in_thread(Scenario()) if live else _dead_server()
    with server as base_url:
        init = runner.invoke(
            app,
            [*INIT, "--base-url", base_url, "--model", model, "--no-password", str(tmp_path)],
        )
        deps = DoctorDeps(docker_probe=_no_docker_needed, environ={})
        doctor = runner.invoke(
            app, ["doctor", "--manifest", str(tmp_path / "hive.toml"), "--json"], obj=deps
        )

    assert init.exit_code == 0, init.output
    assert doctor.exit_code == exit_code, doctor.output
    checks = {check["name"]: check["outcome"] for check in json.loads(doctor.output)["checks"]}
    assert checks["provider local"] == provider
    # Every [llm.slots] row ends the same way: one model, all on one server.
    assert {outcome for name, outcome in checks.items() if name.startswith("model ")} == {models}
    # The starter's own host and configuration checks hold on any machine running the suite.
    assert checks["scratch root"] == "PASSED"
    assert checks["escalation policy"] == "PASSED"
    assert checks["forage"] == "PASSED"
