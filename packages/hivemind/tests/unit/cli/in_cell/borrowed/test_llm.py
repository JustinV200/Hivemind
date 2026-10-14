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
from builders.borrowed import SERVER_ID, device_manifest_text

from hivemind.cli.in_cell.borrowed.llm import device_llm
from hivemind.cli.stores import provider_configs, slot_bindings
from hivemind.common.errors import ConfigurationError
from hivemind.llm import RateLimit
from hivemind.manifest import load_device_manifest, load_manifest
from hivemind.manifest.schema import LlmSection
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


def _section(tmp_path: Path, text: str) -> LlmSection:
    """Load `text` as a device manifest and return its `[llm]` section."""
    path = tmp_path / "warden.toml"
    path.write_text(text, encoding="utf-8")
    return load_device_manifest(path).llm


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

    device = device_llm(_section(tmp_path, text))

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

    device = device_llm(_section(tmp_path, text))

    assert device.seats == {"shared": 3}
    assert device.limits == {"shared": RateLimit(requests_per_minute=30, tokens_per_minute=9000)}


def test_a_declared_server_is_reported_by_id_with_nothing_loaded(tmp_path: Path) -> None:
    device = device_llm(_section(tmp_path, device_manifest_text(with_server=True)))

    assert device.servers == (ModelServerReport(server_id=SERVER_ID, sources=()),)


def test_a_manifest_that_declares_no_server_reports_none(tmp_path: Path) -> None:
    assert device_llm(_section(tmp_path, device_manifest_text())).servers == ()


def test_more_servers_than_one_capacity_report_carries_is_refused_at_start(
    tmp_path: Path,
) -> None:
    servers = "".join(
        f'[llm.servers.s{n}]\nprovider = "local"\nargv = ["srv"]\nport = 8081\nsources = ["x"]\n\n'
        for n in range(9)
    )
    text = device_manifest_text(with_server=True).split(f"[llm.servers.{SERVER_ID}]")[0]
    section = _section(tmp_path, text + servers)

    with pytest.raises(ConfigurationError, match="declares 9 model servers"):
        device_llm(section)


def test_offline_forces_in_process_embedders_to_stay_off_the_network(tmp_path: Path) -> None:
    # `embedding_local_files_only` follows the section's offline flag, as the Hive Stand's does.
    text = device_manifest_text().replace("[llm]\n", "[llm]\noffline = true\n", 1)
    text = text.replace("http://hive.example.lan:1234/v1", "http://127.0.0.1:1234/v1")

    device = device_llm(_section(tmp_path, text))

    assert device.offline is True
    assert device.providers["shared"].embedding_local_files_only is True
