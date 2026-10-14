"""Tests for hivemind.cli.swarm.invite: `hive swarm invite`.

Fits into the Hive:
    Mirrors src/hivemind/cli/swarm/invite.py (codingrules section 3). Drives the real typer app
    against a real `swarm_manifest` Hive and its SQLite file (`harness`). The invite string is
    decoded with the same parser a device's packet is held to, the registry is read back the way a
    running Hive would find it, and the properties asserted are the ceremony's: the string is
    printed once and alone, only its hash is kept, the trail records the label, the cap, the tier
    and the expiry and nothing secret, and the address half names the listener the manifest
    configures.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.cli.swarm.invite for the module under test.
    - docs/waggle/enrolment.md section 3 for the command and the string it prints.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from builders.swarm import make_tls_material
from unit.cli.swarm.harness import invites, invoke, make_hive, trail_events

from hivemind.cell import AccessLevel, CombShieldLevel
from hivemind.common.secrets import FileSecretStore, load_or_mint_hive_signer
from hivemind.manifest import load_manifest
from hivemind.swarm import InviteState, parse_invite_string
from hivemind.swarm.enrolment import token_hash
from waggle.signing import public_key_hex


def _secrets_public_key(manifest: Path) -> str:
    """The Hive's public key as the secret store holds it, which every invite must carry."""
    loaded = load_manifest(manifest, {})
    store = FileSecretStore(loaded.resolve_path(loaded.hive.secrets_dir))
    return public_key_hex(asyncio.run(load_or_mint_hive_signer(store)).public_key_bytes)


def test_invite_prints_the_invite_string_alone_on_standard_output(tmp_path: Path) -> None:
    manifest = make_hive(tmp_path)

    result = invoke(manifest, "invite", "--label", "kitchen-pi")

    assert result.exit_code == 0, result.stderr
    invite = result.stdout.strip()
    assert result.stdout.count("\n") == 1 and invite.startswith("pollen1.")
    # Standard error carries the sentence around it, and never the string itself.
    assert "kitchen-pi" in result.stderr and "pollen enrol -" in result.stderr
    assert invite not in result.stderr


def test_invite_names_the_listener_the_manifest_configures_and_the_hive_key(tmp_path: Path) -> None:
    manifest = make_hive(tmp_path, listen_port=8123)
    loaded = load_manifest(manifest, {})

    result = invoke(manifest, "invite", "--label", "kitchen-pi")

    contents = parse_invite_string(result.stdout)
    assert contents.endpoint.url == "ws://127.0.0.1:8123" and contents.endpoint.tls_sha256 is None
    assert contents.endpoint.hive_id == loaded.hive.id
    assert contents.endpoint.hive_node_id == loaded.hive.node_id
    assert contents.endpoint.hive_key_hex == _secrets_public_key(manifest)
    assert contents.label == "kitchen-pi" and contents.expires_at > datetime.now(UTC)


def test_invite_over_tls_pins_the_certificate_the_listener_serves(tmp_path: Path) -> None:
    material = make_tls_material(tmp_path, "hive", hosts=("hive.example.net",))
    manifest = make_hive(
        tmp_path,
        listen_host="0.0.0.0",  # noqa: S104  # The schema's rule: a non-loopback bind needs TLS.
        tls_cert=material.cert_path.name,
        tls_key=material.key_path.name,
        advertise_url="wss://hive.example.net:8720",
    )

    result = invoke(manifest, "invite")

    endpoint = parse_invite_string(result.stdout).endpoint
    assert endpoint.url == "wss://hive.example.net:8720"
    assert endpoint.tls_sha256 == material.sha256


def test_invite_stores_only_the_tokens_hash_with_the_cap_and_the_tier(tmp_path: Path) -> None:
    manifest = make_hive(tmp_path)

    result = invoke(
        manifest, "invite", "--access", "SCRATCH", "--shield", "Propolis", "--label", "attic-pc"
    )

    token = parse_invite_string(result.stdout).token
    (stored,) = invites(manifest)
    assert stored.code_hash == token_hash(token) and stored.state is InviteState.ISSUED
    assert (stored.access_level, stored.comb_shield) == (
        AccessLevel.SCRATCH,
        CombShieldLevel.PROPOLIS,
    )
    assert stored.label == "attic-pc" and stored.device_id is None


def test_invite_records_the_label_cap_tier_and_expiry_as_the_human_and_nothing_secret(
    tmp_path: Path,
) -> None:
    manifest = make_hive(tmp_path)

    result = invoke(manifest, "invite", "--access", "read_only", "--label", "attic-pc")

    (event,) = trail_events(manifest, "swarm.invited")
    assert event.actor == "human"
    assert event.payload["label"] == "attic-pc"
    assert (event.payload["access_level"], event.payload["comb_shield"]) == ("READ_ONLY", "MEADOW")
    assert event.payload["expires_at"] == invites(manifest)[0].expires_at.isoformat()
    # Neither the string nor its token is anywhere on the trail.
    token = parse_invite_string(result.stdout).token
    recorded = json.dumps(event.payload)
    assert token not in recorded and result.stdout.strip() not in recorded


def test_invite_without_options_grants_up_to_the_manifests_default_access_on_meadow(
    tmp_path: Path,
) -> None:
    manifest = make_hive(tmp_path, default_access="SCRATCH")

    result = invoke(manifest, "invite")

    assert result.exit_code == 0, result.stderr
    (stored,) = invites(manifest)
    assert (stored.access_level, stored.comb_shield) == (
        AccessLevel.SCRATCH,
        CombShieldLevel.MEADOW,
    )
    assert stored.label is None


def test_two_invites_are_two_tokens_of_one_hive(tmp_path: Path) -> None:
    manifest = make_hive(tmp_path)

    first = parse_invite_string(invoke(manifest, "invite").stdout)
    second = parse_invite_string(invoke(manifest, "invite").stdout)

    assert first.token != second.token
    assert first.endpoint == second.endpoint
    assert len(invites(manifest)) == 2


def test_invite_refuses_night_veil_with_the_reason_and_mints_nothing(tmp_path: Path) -> None:
    manifest = make_hive(tmp_path)

    result = invoke(manifest, "invite", "--shield", "night_veil")

    assert result.exit_code == 1 and result.stdout == ""
    assert result.stderr.startswith("hive.swarm.invite_refused")
    assert "Virtual Cells" in result.stderr
    assert invites(manifest) == ()


@pytest.mark.parametrize(
    "bad", [("--access", "root"), ("--shield", "granite"), ("--label", "x" * 65)]
)
def test_invite_rejects_a_name_it_does_not_know_or_a_label_too_long_as_bad_input(
    tmp_path: Path, bad: tuple[str, str]
) -> None:
    manifest = make_hive(tmp_path)

    result = invoke(manifest, "invite", *bad)

    assert result.exit_code == 2 and result.stdout == ""
    assert invites(manifest) == ()


def test_invite_refuses_while_the_manifest_keeps_the_swarm_off(tmp_path: Path) -> None:
    manifest = make_hive(tmp_path, enabled=False)

    result = invoke(manifest, "invite")

    assert result.exit_code == 1 and result.stdout == ""
    assert "[swarm] enabled = false" in result.stderr


def test_invite_exits_2_on_a_missing_manifest(tmp_path: Path) -> None:
    result = invoke(tmp_path / "missing.toml", "invite")

    assert result.exit_code == 2
