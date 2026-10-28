"""Tests for hivemind.cli.swarm.packet: `hive swarm packet`.

Fits into the Hive:
    Mirrors src/hivemind/cli/swarm/packet.py (codingrules section 3). Drives the real typer app
    against a real `swarm_manifest` Hive and its SQLite file (`harness`), with a cached pollen
    build on disk and Tailscale's minter swapped for `FakeTailnetKeys` at the command's own
    composition name, so nothing is dialled. What is asserted is the command's promise: the folder
    on standard output and nothing secret on either stream or on the trail.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.cli.swarm.packet for the module under test.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from builders.swarm import make_tls_material
from unit.cli.swarm.harness import invoke, make_hive, trail_events

from hivemind.swarm.packet import AUTH_KEY_FILE, INVITE_FILE, SECRETS_DIR, FakeTailnetKeys

_HOST = "hivestand.example-tailnet.ts.net"


def test_packet_prints_only_the_folder_and_keeps_secrets_off_both_streams_and_the_trail(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Arrange a Hive whose devices dial its MagicDNS name over TLS, an OAuth source named in the
    # manifest, a cached Windows build, and a fake Tailscale in place of the real API client.
    material = make_tls_material(tmp_path, "hive", hosts=(_HOST,))
    manifest = make_hive(
        tmp_path,
        listen_host="100.101.102.103",  # The overlay address; a non-loopback bind needs TLS.
        tls_cert=material.cert_path.name,
        tls_key=material.key_path.name,
        advertise_url=f"wss://{_HOST}:8720",
    )
    text = manifest.read_text(encoding="utf-8")
    packet_table = '[swarm.packet]\ntailscale_oauth = "env:TS_TEST"\npollen_dir = "builds"\n'
    manifest.write_text(f"{text}\n{packet_table}", encoding="utf-8")
    build = tmp_path / "builds" / "windows-x86_64" / "pollen.exe"
    build.parent.mkdir(parents=True)
    build.write_bytes(b"pollen build")
    keys = FakeTailnetKeys()
    monkeypatch.setattr("hivemind.cli.swarm.packet.TailscaleApiKeys", lambda *_: keys)
    folder = tmp_path / "kitchen-packet"

    result = invoke(
        manifest, "packet", "--label", "kitchen-pc", "--platform", "windows-x86_64",
        "--access", "scratch", "--out", str(folder),
    )  # fmt: skip

    assert result.exit_code == 0, result.stderr
    assert result.stdout.strip() == str(folder)
    secrets = [
        (folder / SECRETS_DIR / name).read_text(encoding="utf-8").strip()
        for name in (INVITE_FILE, AUTH_KEY_FILE)
    ]
    recorded = json.dumps([e.payload for e in trail_events(manifest, "swarm.packet_built")])
    assert recorded != "[]"
    for secret in secrets:
        assert secret not in result.stdout and secret not in result.stderr
        assert secret not in recorded
    assert _HOST in (folder / "pollinate.ps1").read_text(encoding="utf-8")
