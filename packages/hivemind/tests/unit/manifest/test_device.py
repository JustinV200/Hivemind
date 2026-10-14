"""Tests for hivemind.manifest.device: DeviceManifest and load_device_manifest.

Fits into the Hive:
    Mirrors src/hivemind/manifest/device.py (codingrules section 3: tests/unit mirrors src/
    one-to-one).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.manifest.device for the module under test.
    - docs/manifests/device.toml for the shipped example the first test loads.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hivemind.forage import ModelSlot
from hivemind.manifest import (
    DeviceManifest,
    DeviceSupervisionSection,
    ManifestError,
    load_device_manifest,
)

# The repository root, five parents up from this test file
# (packages/hivemind/tests/unit/manifest/test_device.py), as test_loader.py finds it.
_EXAMPLE = Path(__file__).resolve().parents[5] / "docs" / "manifests" / "device.toml"


def _write(tmp_path: Path, text: str) -> Path:
    """Write `text` as a device manifest under `tmp_path` and return its path."""
    path = tmp_path / "warden.toml"
    path.write_text(text, encoding="utf-8")
    return path


def _llm_text(extra: str = "") -> str:
    """A complete `[llm]` section: one local provider with every slot bound to it, then `extra`."""
    slots = "".join(
        f'[llm.slots.{slot.manifest_key}]\nprovider = "shared"\nmodel = "m"\n\n'
        for slot in ModelSlot
    )
    provider = '[llm.providers.shared]\nkind = "openai_compat"\nbase_url = "http://hive.lan/v1"\n\n'
    return f"[llm]\n\n{provider}{slots}{extra}"


def _server_text(row: str = "") -> str:
    """A complete `[llm]` declaring one local server that lists the model "x", then `row`."""
    local = (
        '[llm.providers.local]\nkind = "openai_compat"\nbase_url = "http://127.0.0.1:8081/v1"\n\n'
    )
    server = '[llm.servers.s]\nprovider = "local"\nargv = ["srv"]\nport = 8081\nsources = ["x"]\n\n'
    return _llm_text(local + server) + row


_ROW = '[forage.map.x]\nprovider = "local"\nmodel = "x-model"\ngrade = 2\ncontext_window = 4096\n'


def test_the_shipped_example_loads_and_declares_a_server() -> None:
    manifest = load_device_manifest(_EXAMPLE)

    assert set(manifest.llm.providers) == {"shared", "local"}
    assert set(manifest.llm.servers) == {"local_llama"}
    assert set(manifest.forage.map) == {"local_llama"}  # The model that server may load.
    assert all(slot.manifest_key in manifest.llm.slots for slot in ModelSlot)
    # It states the shipped defaults, so an operator sees the figures they would be changing.
    assert manifest.supervision == DeviceSupervisionSection()


def test_a_complete_llm_section_is_a_device_manifest(tmp_path: Path) -> None:
    manifest = load_device_manifest(_write(tmp_path, _llm_text()))

    assert isinstance(manifest, DeviceManifest)
    assert manifest.llm.providers["shared"].base_url == "http://hive.lan/v1"
    assert manifest.llm.servers == {}  # Declaring a server is the operator's choice.
    assert manifest.forage.map == {}  # And with none, there is no model to describe.


def test_any_other_section_is_refused_not_ignored(tmp_path: Path) -> None:
    path = _write(tmp_path, _llm_text("[hive_stand]\nenabled = true\n"))

    with pytest.raises(ManifestError, match=r"hive_stand.*Extra inputs are not permitted"):
        load_device_manifest(path)


def test_a_file_with_no_llm_section_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ManifestError, match="llm"):
        load_device_manifest(_write(tmp_path, ""))


def test_an_llm_section_that_leaves_a_slot_unbound_is_refused(tmp_path: Path) -> None:
    text = _llm_text().replace('[llm.slots.transcriber]\nprovider = "shared"\nmodel = "m"\n', "")

    with pytest.raises(ManifestError, match="missing a binding for: transcriber"):
        load_device_manifest(_write(tmp_path, text))


def test_a_server_whose_provider_is_not_loopback_is_refused(tmp_path: Path) -> None:
    # The Hive Manifest's own rule, unchanged: a model server answers on this machine's loopback.
    server = '[llm.servers.s]\nprovider = "shared"\nargv = ["srv"]\nport = 80\nsources = ["x"]\n'

    with pytest.raises(ManifestError, match="loopback"):
        load_device_manifest(_write(tmp_path, _llm_text(server)))


def test_a_missing_file_and_bad_toml_name_the_file(tmp_path: Path) -> None:
    missing = tmp_path / "absent.toml"
    with pytest.raises(ManifestError, match="Could not read device manifest"):
        load_device_manifest(missing)

    broken = _write(tmp_path, "[llm\n")
    with pytest.raises(ManifestError, match=r"warden\.toml is not valid TOML"):
        load_device_manifest(broken)


def test_a_server_and_the_map_row_of_the_model_it_lists_are_a_device_manifest(
    tmp_path: Path,
) -> None:
    manifest = load_device_manifest(_write(tmp_path, _server_text(_ROW)))

    assert manifest.forage.map["x"].model == "x-model"  # What the Warden loads, and as what.


def test_a_server_listing_a_model_with_no_map_row_is_refused(tmp_path: Path) -> None:
    # The Warden could start the server but not say what it serves: caught at load, not at the
    # promotion the Queen asks for in the middle of an outage.
    with pytest.raises(ManifestError, match=r"lists source 'x', which has no \[forage\.map\.x\]"):
        load_device_manifest(_write(tmp_path, _server_text()))


def test_a_map_row_served_by_another_provider_than_the_servers_is_refused(tmp_path: Path) -> None:
    row = _ROW.replace('provider = "local"', 'provider = "shared"')

    with pytest.raises(ManifestError, match="is served by provider 'shared', not 'local'"):
        load_device_manifest(_write(tmp_path, _server_text(row)))


def test_a_map_row_no_server_lists_is_refused(tmp_path: Path) -> None:
    # A row nothing reads is a typo or a stale copy of the Hive's manifest: surfaced, not kept.
    with pytest.raises(ManifestError, match=r"\[forage\.map\.y\] is listed by no \[llm\.servers\]"):
        load_device_manifest(_write(tmp_path, _server_text(_ROW) + _ROW.replace(".x]", ".y]")))


def test_a_map_row_that_names_a_host_cell_is_refused(tmp_path: Path) -> None:
    # The Warden fills in its own Cell when it loads the model; a row that names another would
    # claim the model is served from somewhere else.
    row = _ROW + 'host_cell_id = "cell_01M221E2YZ76H25M1TTCEJTZQN"\n'

    with pytest.raises(ManifestError, match="names a host Cell"):
        load_device_manifest(_write(tmp_path, _server_text(row)))


def test_a_forage_section_may_hold_nothing_but_the_map(tmp_path: Path) -> None:
    # Only the models a device's servers load are described here; the Hive's roles, ceilings and
    # the rest of `[forage]` stay on the Hive Stand, and a key the Warden would not read is refused.
    with pytest.raises(ManifestError, match=r"forage\.roles.*Extra inputs are not permitted"):
        load_device_manifest(_write(tmp_path, _server_text(_ROW) + "[forage.roles.drone]\n"))


def test_a_manifest_with_no_supervision_waits_as_long_as_the_hive_ships(tmp_path: Path) -> None:
    manifest = load_device_manifest(_write(tmp_path, _llm_text()))

    # The Hive's own defaults: ten minutes, and two Drones in five of them.
    assert manifest.supervision.max_offline_s == 600.0
    assert manifest.supervision.reconnect_budget.attempts == 2
    assert manifest.supervision.reconnect_budget.wall_clock_s == 300.0


def test_supervision_sets_the_offline_limit_and_the_budget_carved_out_of_it(tmp_path: Path) -> None:
    table = "[supervision]\nmax_offline_s = 90\n[supervision.reconnect_budget]\n"
    table += "attempts = 3\nwall_clock_s = 40\n"

    manifest = load_device_manifest(_write(tmp_path, _llm_text() + table))

    assert manifest.supervision.max_offline_s == 90.0
    assert manifest.supervision.reconnect_budget.attempts == 3
    assert manifest.supervision.reconnect_budget.wall_clock_s == 40.0


def test_a_limit_alone_is_enough_when_the_default_budget_still_fits_inside_it(
    tmp_path: Path,
) -> None:
    manifest = load_device_manifest(
        _write(tmp_path, _llm_text("[supervision]\nmax_offline_s=900\n"))
    )

    assert manifest.supervision.max_offline_s == 900.0
    assert manifest.supervision.reconnect_budget.wall_clock_s == 300.0  # Left as shipped.


@pytest.mark.parametrize(
    ("limit", "budget"),
    [(300, 300), (200, 300)],  # Equal is as bad as longer: nothing would be left to cluster with.
)
def test_a_budget_not_below_the_offline_limit_is_refused_as_the_hives_is(
    tmp_path: Path, limit: int, budget: int
) -> None:
    table = f"[supervision]\nmax_offline_s = {limit}\n"
    table += f"[supervision.reconnect_budget]\nwall_clock_s = {budget}\n"

    with pytest.raises(ManifestError, match=r"must be below \[supervision\] max_offline_s"):
        load_device_manifest(_write(tmp_path, _llm_text() + table))


def test_a_lowered_limit_with_the_default_budget_is_refused_naming_both_figures(
    tmp_path: Path,
) -> None:
    # The mistake an operator makes first: they shorten the wait and forget the budget beside it.
    path = _write(tmp_path, _llm_text("[supervision]\nmax_offline_s = 120\n"))

    with pytest.raises(ManifestError, match=r"wall_clock_s \(300\).*max_offline_s \(120\)"):
        load_device_manifest(path)


@pytest.mark.parametrize(
    "line",
    ["max_offline_s = 0", "max_offline_s = -5", "policy_file = 'policy.toml'", "watch = 1"],
)
def test_a_supervision_key_the_warden_does_not_read_or_cannot_use_is_refused(
    tmp_path: Path, line: str
) -> None:
    # A key the Warden would silently not read is a mistake worth surfacing, as for any section.
    with pytest.raises(ManifestError, match="supervision"):
        load_device_manifest(_write(tmp_path, _llm_text(f"[supervision]\n{line}\n")))


@pytest.mark.parametrize("line", ["attempts = 0", "wall_clock_s = 0", "wall_clock_s = -1"])
def test_a_reconnect_budget_figure_that_is_not_positive_is_refused(
    tmp_path: Path, line: str
) -> None:
    table = f"[supervision]\nmax_offline_s = 90\n[supervision.reconnect_budget]\n{line}\n"

    with pytest.raises(ManifestError, match="reconnect_budget"):
        load_device_manifest(_write(tmp_path, _llm_text() + table))
