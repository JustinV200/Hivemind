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
from hivemind.manifest import DeviceManifest, ManifestError, load_device_manifest

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


def test_the_shipped_example_loads_and_declares_a_server() -> None:
    manifest = load_device_manifest(_EXAMPLE)

    assert set(manifest.llm.providers) == {"shared", "local"}
    assert set(manifest.llm.servers) == {"local_llama"}
    assert all(slot.manifest_key in manifest.llm.slots for slot in ModelSlot)


def test_a_complete_llm_section_is_a_device_manifest(tmp_path: Path) -> None:
    manifest = load_device_manifest(_write(tmp_path, _llm_text()))

    assert isinstance(manifest, DeviceManifest)
    assert manifest.llm.providers["shared"].base_url == "http://hive.lan/v1"
    assert manifest.llm.servers == {}  # Declaring a server is the operator's choice.


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
