"""Test hivemind.cli.serve: `hive serve` exits cleanly with the reason when it cannot serve.

Fits into the Hive:
    Test infrastructure (codingrules section 14.2), not shipped. Runs the real command through
    `typer.testing.CliRunner`, the way an operator's shell would.

Key invariants:
    - None: this module holds tests only.
"""

from __future__ import annotations

import socket
from pathlib import Path

from builders.cli import fake_manifest
from typer.testing import CliRunner

from hivemind.cli.app import app

runner = CliRunner()


def _manifest(tmp_path: Path, entrance: str) -> Path:
    """Write a fake manifest with ``entrance`` as its ``[entrance]`` section."""
    path = fake_manifest(tmp_path)
    path.write_text(
        path.read_text(encoding="utf-8") + f"\n[entrance]\n{entrance}", encoding="utf-8"
    )
    return path


def test_hive_serve_exits_2_naming_the_rule_when_the_exposure_is_refused(tmp_path: Path) -> None:
    path = _manifest(
        tmp_path,
        'bind = "127.0.0.1:0"\nexpose = "lan"\nremote_bind = "192.168.1.20:8711"\n'
        'mutual_tls = false\npublic_url = "https://hive.example.net"\n',
    )

    result = runner.invoke(app, ["serve", "--manifest", str(path)])

    assert result.exit_code == 2
    assert "refused to expose" in result.output
    assert "mutual_tls" in result.output


def test_hive_serve_exits_1_when_the_loopback_listener_cannot_bind(tmp_path: Path) -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as taken:
        taken.bind(("127.0.0.1", 0))
        taken.listen(1)
        path = _manifest(tmp_path, f'bind = "127.0.0.1:{taken.getsockname()[1]}"\n')

        result = runner.invoke(app, ["serve", "--manifest", str(path)])

    assert result.exit_code == 1
    assert "hive serve failed: OSError" in result.output


def test_hive_serve_is_a_registered_command() -> None:
    result = runner.invoke(app, ["serve", "--help"])

    assert result.exit_code == 0
    assert "--manifest" in result.output
