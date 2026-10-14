"""Tests for hivemind.royal_jelly.spec.package: deterministic packing and a hostile-input unpack.

Fits into the Hive:
    Mirrors src/hivemind/royal_jelly/spec/package.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.royal_jelly.spec.package for the module under test.
"""

from __future__ import annotations

import io
import struct
import sys
import zipfile

import pytest
from builders.royal_jelly import make_package, make_package_files

from hivemind.royal_jelly.errors import PackageError
from hivemind.royal_jelly.spec import (
    MAX_PACKAGE_BYTES,
    SPEC_FILE,
    TESTS_MODULE,
    TOOL_MODULE,
    ToolPackage,
    pack,
    required_files_present,
    sha256_of,
    unpack,
)


def _zip(entries: list[tuple[str, bytes]], *, compression: int = zipfile.ZIP_DEFLATED) -> bytes:
    """Build a zip with arbitrary (possibly hostile) entry names, bypassing `pack`'s checks."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression) as archive:
        for name, content in entries:
            # ZipInfo turns the host's separator into `/` at construction, so a backslash would
            # vanish on Windows; the name is set afterwards to reach the archive untouched.
            info = zipfile.ZipInfo("placeholder")
            info.filename = name
            info.compress_type = compression  # A ZipInfo ignores the archive's default.
            archive.writestr(info, content)
    return buffer.getvalue()


def test_two_packs_of_the_same_mapping_are_byte_identical() -> None:
    files = make_package_files()

    assert pack(files) == pack(dict(files))
    assert sha256_of(pack(files)) == sha256_of(pack(files))


def test_pack_ignores_the_mappings_insertion_order() -> None:
    files = make_package_files()
    reordered = dict(reversed(list(files.items())))

    assert pack(files) == pack(reordered)


def test_pack_uses_sorted_paths_fixed_timestamps_and_fixed_attributes() -> None:
    data = make_package()

    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        infos = archive.infolist()

    assert [info.filename for info in infos] == sorted(info.filename for info in infos)
    assert {info.date_time for info in infos} == {(1980, 1, 1, 0, 0, 0)}
    assert {info.external_attr for info in infos} == {0o644 << 16}
    assert {info.compress_type for info in infos} == {zipfile.ZIP_DEFLATED}


def test_a_different_file_changes_the_hash() -> None:
    files = make_package_files()

    changed = {**files, TOOL_MODULE: files[TOOL_MODULE] + b"# x\n"}

    assert sha256_of(pack(changed)) != sha256_of(pack(files))


def test_unpack_returns_what_pack_was_given() -> None:
    files = make_package_files()

    assert dict(unpack(pack(files))) == files


def test_tool_package_namespace_delegates_to_the_functions() -> None:
    files = make_package_files()
    data = ToolPackage.pack(files)

    assert data == pack(files)
    assert dict(ToolPackage.unpack(data)) == files
    assert ToolPackage.sha256_of(data) == sha256_of(data)
    assert ToolPackage.MAX_BYTES == MAX_PACKAGE_BYTES


def test_required_files_present_names_the_missing_ones() -> None:
    files = make_package_files()

    assert required_files_present(files) == ()
    assert required_files_present({TOOL_MODULE: b""}) == (TESTS_MODULE, SPEC_FILE)
    assert required_files_present({}) == (TOOL_MODULE, TESTS_MODULE, SPEC_FILE)


@pytest.mark.parametrize(
    "path",
    [
        "/etc/passwd",
        "../escape.py",
        "a/../../escape.py",
        "a/./b.py",
        "a//b.py",
        "a\\b.py",
        "C:/windows.py",
        "dir/",
        "",
        "x" * 300,
    ],
)
def test_pack_and_unpack_both_refuse_an_unsafe_path(path: str) -> None:
    with pytest.raises(PackageError):
        pack({path: b"x"})
    if "\\" in path and sys.platform == "win32":
        # zipfile turns a backslash into `/` when it reads an entry name on Windows, so unpack
        # is handed `a/b.py`, which is safe, and the check cannot be reached there.
        return
    with pytest.raises(PackageError):
        unpack(_zip([(path, b"x")]))


def test_unpack_refuses_a_duplicate_entry() -> None:
    with pytest.warns(UserWarning, match="Duplicate name"):
        data = _zip([("tool.py", b"first"), ("tool.py", b"second")])

    with pytest.raises(PackageError, match="twice"):
        unpack(data)


def test_unpack_refuses_input_that_is_not_a_zip() -> None:
    with pytest.raises(PackageError, match="not a valid zip"):
        unpack(b"definitely not a zip")


def test_unpack_refuses_a_zip_bomb_by_declared_size() -> None:
    # Highly compressible: tiny on the wire, over the cap once inflated.
    data = _zip([("tool.py", b"\x00" * (MAX_PACKAGE_BYTES + 1))])

    assert len(data) < MAX_PACKAGE_BYTES
    with pytest.raises(PackageError, match="declares"):
        unpack(data)


def test_unpack_refuses_a_zip_bomb_split_across_entries() -> None:
    half = MAX_PACKAGE_BYTES // 2 + 1
    data = _zip([("a.py", b"\x00" * half), ("b.py", b"\x00" * half)])

    with pytest.raises(PackageError):
        unpack(data)


def test_unpack_checks_the_bytes_actually_read_not_only_the_declared_sizes() -> None:
    # Forge the central directory so the declared size says 10 bytes while the entry inflates to
    # more than the cap; the read-time bound must still catch it (or the codec must object).
    data = bytearray(_zip([("tool.py", b"\x00" * (MAX_PACKAGE_BYTES + 1))]))
    for marker, offset in ((b"PK\x01\x02", 24), (b"PK\x03\x04", 22)):
        start = data.index(marker)
        data[start + offset : start + offset + 4] = struct.pack("<I", 10)

    with pytest.raises(PackageError):
        unpack(bytes(data))


def test_unpack_refuses_an_unsupported_compression_codec() -> None:
    data = _zip([("tool.py", b"x")], compression=zipfile.ZIP_BZIP2)

    with pytest.raises(PackageError, match="codec"):
        unpack(data)


def test_unpack_refuses_a_corrupt_entry_body() -> None:
    data = bytearray(make_package())
    # Flip a byte inside the first entry's compressed body so its CRC no longer matches.
    data[40] ^= 0xFF

    with pytest.raises(PackageError):
        unpack(bytes(data))


def test_pack_refuses_content_over_the_cap() -> None:
    with pytest.raises(PackageError, match="exceeds"):
        pack({"tool.py": b"x" * (MAX_PACKAGE_BYTES + 1)})


def test_pack_refuses_too_many_files() -> None:
    with pytest.raises(PackageError, match="files"):
        pack({f"f{i}.py": b"x" for i in range(65)})


def test_unpack_refuses_an_archive_larger_than_the_cap() -> None:
    with pytest.raises(PackageError, match="limit"):
        unpack(b"\x00" * (MAX_PACKAGE_BYTES + 1))
