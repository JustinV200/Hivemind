"""Test that the environment a Pollen Packet gives its device's Warden is the one the Warden reads.

When the Queen colonizes a device the packet (`pollen.bootstrap`) starts `hivemind-in-cell` with a
handful of `HIVEMIND_*` variables, the device's own key as a file and the operator's device
manifest. The two ends live in different packages that must not import each other (pollen imports
nothing from hivemind), so nothing but this test notices when one drifts from the other: a variable
renamed on one side, a key written in another encoding, a level spelled differently. The test
builds the state a real enrolment leaves, writes the key with the packet's own routine, asks
`pollen.bootstrap.warden_environment` for the variables and feeds them, unchanged, to
`read_in_cell_env` and `build_borrowed_config`. It then checks that the Warden would sign as the
device with that key, trust the Queen's, lease the machine with the level and tier the Queen
granted, and read every variable the packet sets.

Fits into the Hive:
    Contract test (codingrules section 14.3): one wire between two packages, checked on real values
    from both. Mirrors nothing in src/; the modules it joins are `pollen.bootstrap.warden` and
    `hivemind.cli.in_cell.borrowed.config`.

Key invariants:
    - The environment is the packet's real one for the state, the key file is the packet's real
      one, and neither is adjusted on its way to the Warden.
    - A variable the packet sets and the Warden does not read is a failure, not a no-op: a typo in
      one name would otherwise leave a default in its place without a sound.

See Also:
    - pollen.bootstrap.warden for the environment the packet sets.
    - hivemind.cli.in_cell.borrowed.config for the environment the Warden reads.
    - contracts.test_pollen_warden_process for the same wire with a real process on it.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from builders.pollen_packet import HOSTED, LOOPBACK, PIN, Packet, make_packet

from hivemind.cli.in_cell.borrowed import build_borrowed_config, is_borrowed
from hivemind.manifest import read_in_cell_env
from pollen.bootstrap import warden_environment
from waggle.clock import SystemClock
from waggle.codec import canonical_bytes
from waggle.messages.labels import AccessLevel
from waggle.signing import Ed25519Verifier


def _environ(packet: Packet) -> dict[str, str]:
    """The variables the packet starts its Warden with, for the listener the state names."""
    return warden_environment(
        packet.state,
        packet.layout.key,
        packet.layout.warden_state,
        packet.state.queen_url,
        packet.manifest,
    )


def test_the_environment_the_packet_sets_builds_the_config_the_warden_runs_on(
    tmp_path: Path,
) -> None:
    packet = make_packet(tmp_path, level=AccessLevel.SCRATCH)
    state = packet.state

    parsed = read_in_cell_env(_environ(packet))
    config = build_borrowed_config(parsed, SystemClock())

    assert is_borrowed(parsed)
    runtime = config.runtime
    assert (runtime.warden_id, runtime.node_id, runtime.hive_id) == (
        state.warden_id,
        state.node_id,
        state.hive_id,
    )  # The Warden that moves onto the device keeps the Warden's ids and signs as the device.
    assert runtime.spawn_config.cell_id == state.cell_id
    assert runtime.queen_waggle_url == LOOPBACK and config.tls_pin is None
    assert config.state_dir == packet.layout.warden_state
    assert config.cell.access_level.value == state.access_level.value
    assert config.cell.comb_shield.value == state.comb_shield.value


def test_the_warden_signs_with_the_packets_key_and_trusts_the_queens(tmp_path: Path) -> None:
    packet = make_packet(tmp_path, level=AccessLevel.FULL)
    state = packet.state
    runtime = build_borrowed_config(read_in_cell_env(_environ(packet)), SystemClock()).runtime

    message = canonical_bytes({"hello": "queen"})
    device = Ed25519Verifier({state.node_id: packet.device_signer.public_key_bytes})
    device.verify(state.node_id, message, runtime.signer.sign(message))
    runtime.verifier.verify(state.hive_node_id, message, packet.queen_signer.sign(message))


@pytest.mark.parametrize("level", list(AccessLevel))
def test_every_access_level_the_queen_can_grant_reaches_the_warden_unchanged(
    tmp_path: Path, level: AccessLevel
) -> None:
    packet = make_packet(tmp_path, level=level)

    config = build_borrowed_config(read_in_cell_env(_environ(packet)), SystemClock())

    assert config.cell.access_level.value == level.value


def test_a_wss_listener_is_dialled_with_the_pin_the_packet_holds(tmp_path: Path) -> None:
    packet = make_packet(tmp_path, url=HOSTED, pin=PIN)

    environ = _environ(packet)
    config = build_borrowed_config(read_in_cell_env(environ), SystemClock())

    assert environ["HIVEMIND_TLS_PIN"] == PIN
    assert config.runtime.queen_waggle_url == HOSTED and config.tls_pin == PIN


def test_the_directories_the_queen_named_reach_the_warden_as_the_packet_was_told_them(
    tmp_path: Path,
) -> None:
    roots = ("/var/log/app", "C:\\Logs\\app", '/srv/with "quotes" and spaces')
    packet = make_packet(tmp_path, watch_roots=roots)

    config = build_borrowed_config(read_in_cell_env(_environ(packet)), SystemClock())

    # In the order the Queen sent them, byte for byte: the Warden reports them back to her.
    assert config.watch_roots == roots == packet.state.watch_roots


def test_a_packet_told_of_no_directories_starts_a_warden_that_watches_none(tmp_path: Path) -> None:
    packet = make_packet(tmp_path)

    environ = _environ(packet)

    assert "HIVEMIND_WATCH_ROOTS" not in environ
    assert build_borrowed_config(read_in_cell_env(environ), SystemClock()).watch_roots == ()


@pytest.mark.parametrize(
    ("url", "pin", "roots"),
    [(LOOPBACK, None, ()), (HOSTED, PIN, ("/var/log",))],
)
def test_the_warden_reads_every_variable_the_packet_sets(
    tmp_path: Path, url: str, pin: str | None, roots: tuple[str, ...]
) -> None:
    environ = _environ(make_packet(tmp_path, url=url, pin=pin, watch_roots=roots))
    whole = read_in_cell_env(environ).model_dump(exclude={"environ"})

    for name in environ:
        without = {other: value for other, value in environ.items() if other != name}
        # Leaving a variable out must change what the Warden understands; if it does not, the
        # packet is setting a name nothing reads.
        assert read_in_cell_env(without).model_dump(exclude={"environ"}) != whole, name
