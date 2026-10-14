"""Tests for hivemind.cli.in_cell.borrowed.llm: the device manifest's `[llm]` as Warden tables.

Fits into the Hive:
    Mirrors src/hivemind/cli/in_cell/borrowed/llm.py (codingrules section 3: tests/unit mirrors
    src/ one-to-one).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.cli.in_cell.borrowed.llm for the module under test.
    - hivemind.cli.stores for the Hive Stand's conversions these must agree with.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from builders.borrowed import LOCAL_MODEL, SERVER_ID, SOURCE_ID, device_manifest_text

from hivemind.cli.in_cell.borrowed.llm import DeviceLlm, device_llm
from hivemind.cli.stores import provider_configs, server_configs, slot_bindings
from hivemind.common.errors import ConfigurationError
from hivemind.llm import RateLimit
from hivemind.manifest import load_device_manifest, load_manifest
from waggle.messages.forage import ModelServerReport

_HIVE_SECTION = (
    '[hive]\nid = "hive_01M221E2YZ76H25M1TTCEJTZQN"\n'
    'node_id = "node_01M221E2YZEFD69JA7FGH26D5B"\nname = "T"\n\n'
)
# A Hive Manifest must give the Drone role a footprint; the device manifest has no such section.
_DRONE_FOOTPRINT = (
    "\n[forage.roles.drone]\ncpu_cores = 0.5\nmemory_bytes = 268435456\n"
    "token_rate_per_minute = 20000\n"
)


def _device(tmp_path: Path, text: str) -> DeviceLlm:
    """Load `text` as a device manifest and convert its `[llm]` with the map beside it."""
    path = tmp_path / "warden.toml"
    path.write_text(text, encoding="utf-8")
    manifest = load_device_manifest(path)
    return device_llm(manifest.llm, manifest.forage.map)


def test_providers_and_slots_are_what_the_hive_stand_builds_from_the_same_rows(
    tmp_path: Path,
) -> None:
    # The device must mean the same thing by a binding as the Hive Stand does: this section
    # loaded as a Hive Manifest's [llm] gives the same tables through the Hive Stand's converters.
    text = device_manifest_text()
    text = text.replace("[llm]\n", "[llm]\ndefault_max_output_tokens = 4096\n", 1)
    text = text.replace(
        '[llm.slots.queen]\nprovider = "shared"\nmodel = "m"\n',
        '[llm.slots.queen]\nprovider = "shared"\nmodel = "m"\nmax_output_tokens = 99\n',
        1,
    )
    hive = tmp_path / "hive.toml"
    hive.write_text(_HIVE_SECTION + text + _DRONE_FOOTPRINT, encoding="utf-8")

    device = _device(tmp_path, text)

    manifest = load_manifest(hive)
    assert device.providers == provider_configs(manifest)
    assert device.slots == slot_bindings(manifest)
    caps = {binding.key: binding.max_output_tokens for binding in device.slots}
    assert caps["queen"] == 99  # A row's own cap wins ...
    assert caps["worker"] == 4096  # ... and a row with none takes the section's.


def test_a_providers_seats_and_rate_limits_are_kept_for_the_fanner(tmp_path: Path) -> None:
    text = device_manifest_text().replace(
        "seats = 2\n", "seats = 3\nrequests_per_minute = 30\ntokens_per_minute = 9000\n", 1
    )

    device = _device(tmp_path, text)

    assert device.seats == {"shared": 3}
    assert device.limits == {"shared": RateLimit(requests_per_minute=30, tokens_per_minute=9000)}


def test_a_declared_server_is_reported_by_id_with_nothing_loaded(tmp_path: Path) -> None:
    device = _device(tmp_path, device_manifest_text(with_server=True))

    assert device.servers == (ModelServerReport(server_id=SERVER_ID, sources=()),)


def test_a_declared_server_is_started_from_the_row_the_hive_stand_would_build(
    tmp_path: Path,
) -> None:
    # Same argv, port, provider and Forage map rows as `cli.stores.server_configs` gives for the
    # same tables in a Hive Manifest, so a server means the same on a Nuc as on the Hive Stand.
    text = device_manifest_text(with_server=True)
    hive = tmp_path / "hive.toml"
    hive.write_text(_HIVE_SECTION + text + _DRONE_FOOTPRINT, encoding="utf-8")

    device = _device(tmp_path, text)

    hive_row = server_configs(load_manifest(hive))[SERVER_ID]
    assert device.hostable == {SERVER_ID: hive_row}
    assert [source.source_id for source in device.hostable[SERVER_ID].sources] == [SOURCE_ID]
    assert device.hostable[SERVER_ID].sources[0].spec.host_cell_id is None  # No Cell hosts it yet.


def test_a_server_never_starts_with_the_warden_whatever_the_row_says(tmp_path: Path) -> None:
    # A device is a Nuc only once the Queen has promoted it; `load_on_start = true` must not make
    # a Level 1 Warden start a model server on a machine the Hive only borrows.
    text = device_manifest_text(with_server=True).replace(
        "load_on_start = false", "load_on_start = true"
    )

    device = _device(tmp_path, text)

    assert device.hostable[SERVER_ID].load_on_start is False


def test_the_servers_a_manifest_does_not_declare_are_not_hostable(tmp_path: Path) -> None:
    device = _device(tmp_path, device_manifest_text())

    assert device.hostable == {}


def test_a_manifest_that_declares_no_server_reports_none(tmp_path: Path) -> None:
    assert _device(tmp_path, device_manifest_text()).servers == ()


def test_more_servers_than_one_capacity_report_carries_is_refused_at_start(
    tmp_path: Path,
) -> None:
    servers = "".join(
        f'[llm.servers.s{n}]\nprovider = "local"\nargv = ["srv"]\nport = 8081\nsources = ["x"]\n\n'
        for n in range(9)
    )
    text = device_manifest_text(with_server=True).split(f"[llm.servers.{SERVER_ID}]")[0]
    row = f'[forage.map.x]\nprovider = "local"\nmodel = "{LOCAL_MODEL}"\ngrade = 2\n'

    with pytest.raises(ConfigurationError, match="declares 9 model servers"):
        _device(tmp_path, text + servers + row + "context_window = 4096\n")


def test_offline_forces_in_process_embedders_to_stay_off_the_network(tmp_path: Path) -> None:
    # `embedding_local_files_only` follows the section's offline flag, as the Hive Stand's does.
    text = device_manifest_text().replace("[llm]\n", "[llm]\noffline = true\n", 1)
    text = text.replace("http://hive.example.lan:1234/v1", "http://127.0.0.1:1234/v1")

    device = _device(tmp_path, text)

    assert device.offline is True
    assert device.providers["shared"].embedding_local_files_only is True
