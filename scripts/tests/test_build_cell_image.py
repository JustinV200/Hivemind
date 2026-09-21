"""Unit tests for scripts/build_cell_image.py.

Fits into the Hive:
    Layer: none (tests for a dev-time build script). QEMU and the ISO-building tools are not
    installed on this dev host (ADR-0026), so this module covers what is testable without them:
    tool-presence detection (`shutil.which` monkeypatched), SHA256 verification against a real
    tmp_path file, the placeholder-digest refusal, and `_build_provisioning_seed`'s own degrade
    when no ISO tool is on PATH. The full download-resize-boot pipeline needs real network access
    and real QEMU, neither available here; a maintainer runs it for real once both exist.

Key invariants:
    - None: this module holds tests, not behaviour.

See Also:
    - scripts/build_cell_image.py, the module under test.
"""

# codingrules 6.2: a sentence-shaped test name is its own docstring, and `assert` is how pytest
# reports failure. scripts/tests/ is not in pyproject.toml's S101/D103 exemption list (roadmap
# 0.3 was not authorised to add to it), so the same exemption is granted per-file here instead.

import hashlib
import shutil
from pathlib import Path

import build_cell_image
import pytest


def test_require_tools_returns_none_when_qemu_img_is_missing(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(shutil, "which", lambda name: None)

    result = build_cell_image._require_tools()

    assert result is None
    assert "qemu-img and qemu-system-x86_64" in capsys.readouterr().out


def test_require_tools_returns_both_paths_when_present(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(shutil, "which", lambda name: f"/usr/bin/{name}")

    result = build_cell_image._require_tools()

    assert result == ("/usr/bin/qemu-img", "/usr/bin/qemu-system-x86_64")


def test_verify_sha256_passes_for_a_matching_digest(tmp_path: Path) -> None:
    target = tmp_path / "image.img"
    target.write_bytes(b"fake cloud image bytes")
    expected = hashlib.sha256(b"fake cloud image bytes").hexdigest()

    build_cell_image._verify_sha256(target, expected)  # Must not raise.
    build_cell_image._verify_sha256(target, expected.upper())  # Case-insensitive.


def test_verify_sha256_raises_for_a_mismatched_digest(tmp_path: Path) -> None:
    target = tmp_path / "image.img"
    target.write_bytes(b"fake cloud image bytes")

    with pytest.raises(ValueError, match="SHA256 mismatch"):
        build_cell_image._verify_sha256(target, "00" * 32)


def test_main_refuses_while_the_sha256_is_still_a_placeholder(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # Tools present (so the flow reaches the placeholder check), but the shipped constant is the
    # documented placeholder until a maintainer refreshes it (module docstring's own key invariant).
    monkeypatch.setattr(shutil, "which", lambda name: f"/usr/bin/{name}")

    exit_code = build_cell_image.main([])

    assert exit_code == 1
    assert "placeholder" in capsys.readouterr().out


def test_main_returns_1_when_qemu_is_missing(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(shutil, "which", lambda name: None)

    exit_code = build_cell_image.main([])

    assert exit_code == 1
    assert "qemu-img and qemu-system-x86_64" in capsys.readouterr().out


def test_build_provisioning_seed_raises_a_clear_error_with_no_iso_tool(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(shutil, "which", lambda name: None)

    with pytest.raises(RuntimeError, match="genisoimage"):
        build_cell_image._build_provisioning_seed(tmp_path)
