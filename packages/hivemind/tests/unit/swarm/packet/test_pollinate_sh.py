"""Tests for templates/pollinate.sh: a machine it cannot work on stops before any secret is used.

Fits into the Hive:
    Covers src/hivemind/swarm/packet/templates/pollinate.sh, which has no Python module of its
    own. A packet is written as `hive swarm packet` writes one and its installer run under bash
    with a fake `tailscale` first on PATH, so nothing is installed and no network is touched. What
    is asserted is the preflight: signed in as anything but the packet's tagged node, connected or
    not, or a daemon that does not answer, stops the script with its advice and leaves `secrets/`
    for the next run.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.swarm.packet.writer for the packet's layout.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import SecretStr

from hivemind.swarm.packet import (
    INVITE_FILE,
    SECRETS_DIR,
    HiveTarget,
    PacketPlatform,
    write_packet,
)
from hivemind.swarm.packet.writer import PacketFiles

_BASH = shutil.which("bash")
# A `tailscale status --json` that a signed-in, untagged node answers, in the given state; with no
# state, a daemon that does not answer (nothing on stdout, exit 1).
_FAKE_TAILSCALE = """#!/bin/sh
[ -n "$FAKE_TS_STATE" ] || exit 1
printf '{\\n  "BackendState": "%s",\\n  "Self": {"Tags": null}\\n}\\n' "$FAKE_TS_STATE"
"""


# Windows is skipped as test_diagnose_rig does: `bash` there may be the WSL launcher, which runs
# another machine's shell and cannot see this PATH.
@pytest.mark.skipif(
    _BASH is None or sys.platform == "win32", reason="needs a POSIX bash on this host"
)
@pytest.mark.parametrize(
    ("state", "advice"),
    [
        ("Running", "tailscale logout"),
        ("Stopped", "tailscale logout"),
        ("", "systemctl start tailscaled"),  # Installed, but the daemon does not answer.
    ],
)
def test_pollinate_sh_stops_before_touching_secrets_when_it_cannot_work(
    tmp_path: Path, state: str, advice: str
) -> None:
    # Arrange a packet written as `hive swarm packet` writes one, and a fake tailscale.
    build = tmp_path / "build"
    build.write_bytes(b"pollen build")
    packet = tmp_path / "packet"
    packet.mkdir()
    target = HiveTarget("hivestand.example-tailnet.ts.net", 8720)
    files = PacketFiles(PacketPlatform.LINUX_X86_64, build, target, "kitchen-pc", datetime.now(UTC))
    write_packet(packet, files, "pollen1.not-real", SecretStr("tskey-auth-not-real"))
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    (fake_bin / "tailscale").write_text(_FAKE_TAILSCALE, encoding="utf-8", newline="\n")
    (fake_bin / "tailscale").chmod(0o755)
    env = {**os.environ, "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}"}
    env["FAKE_TS_STATE"] = state
    env["POLLINATE_DAEMON_WAIT"] = "2"  # Seconds, not the real fifteen.

    # SAFETY: a test-only run of the written installer under bash, with a fake tailscale first on
    # PATH; argv is fixed and nothing reaches a shell string.
    ran = subprocess.run(  # noqa: S603
        [str(_BASH), str(packet / "pollinate.sh")],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )

    assert ran.returncode == 1
    assert "STOPPED" in ran.stderr and advice in ran.stderr
    assert (packet / SECRETS_DIR / INVITE_FILE).is_file()
