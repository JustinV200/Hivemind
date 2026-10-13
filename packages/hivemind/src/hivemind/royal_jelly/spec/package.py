"""Pack and unpack a tool package: a deterministic zip named by its SHA-256.

A tool is shipped as one archive of files: `tool.py` (the entry-point module), `test_tool.py` (its
`unittest` tests) and `spec.json` (the `ToolSpec`). The archive bytes live in the Basket (the
Hive's store of inert blobs); the Comb Registry (the catalogue of promoted tools) stores only the
hash. For one hash to name one immutable version everywhere, packing must be deterministic: sorted
paths, a fixed timestamp, fixed permissions and a fixed compression level, so two packs of the same
files are byte-identical. Unpacking is the dangerous direction (an archive can come from a model's
output), so it refuses absolute paths, `..`, backslashes, duplicate entries and anything that
inflates past `MAX_PACKAGE_BYTES`, and it never writes to disk: it returns bytes in memory.

Fits into the Hive:
    Layer 3 (sources of Cells, and capabilities handed down), inside royal_jelly.spec. Called by
    the scaffolder (pack), the Quarantine Comb and the Worker's tool loader (unpack, sha256_of).
    Calls into the standard library and `hivemind.royal_jelly.errors` only.

Key invariants:
    - `pack` of the same mapping always returns the same bytes (on one zlib build).
    - `unpack` never returns a path that is absolute, contains `..`, a backslash or an empty
      segment, and never returns more than `MAX_PACKAGE_BYTES` of file content in total.
    - Neither function touches the filesystem.

See Also:
    - docs/adr/0046 (tool spec, Comb Registry and scopes) for the package format.
    - hivemind.royal_jelly.errors for PackageError, every refusal here.
"""

from __future__ import annotations

import hashlib
import io
import zipfile
import zlib
from collections.abc import Iterable, Mapping

from hivemind.royal_jelly.errors import PackageError

TOOL_MODULE = "tool.py"  # The entry-point module every package holds.
TESTS_MODULE = "test_tool.py"  # Its unittest tests, run by the Comb's tests gate.
SPEC_FILE = "spec.json"  # The ToolSpec, in its canonical text form.
MAX_PACKAGE_BYTES = 4 * 1024 * 1024  # Total file content: a tool is source text, never data.
MAX_PACKAGE_FILES = 64  # A package is three files plus helpers; this stops an entry-count bomb.
MAX_PATH_CHARS = 255  # One archive path; matches common filesystem limits.

_FIXED_TIMESTAMP = (1980, 1, 1, 0, 0, 0)  # The earliest instant a zip can store.
_FIXED_COMPRESSION_LEVEL = 6  # zlib's default, pinned so a library default change cannot move it.
_FIXED_EXTERNAL_ATTR = 0o644 << 16  # A regular file, rw-r--r--, in the Unix attribute field.
_UNIX_CREATE_SYSTEM = 3  # Pinned: ZipInfo would otherwise record the packing host's own system.
_ALLOWED_COMPRESSION = frozenset({zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED})  # No lzma/bz2 bombs.
_REQUIRED_FILES = (TOOL_MODULE, TESTS_MODULE, SPEC_FILE)

__all__ = [
    "MAX_PACKAGE_BYTES",
    "MAX_PACKAGE_FILES",
    "SPEC_FILE",
    "TESTS_MODULE",
    "TOOL_MODULE",
    "ToolPackage",
    "pack",
    "required_files_present",
    "sha256_of",
    "unpack",
]


def pack(files: Mapping[str, bytes]) -> bytes:
    """Build the deterministic archive of `files`.

    Args:
        files: Relative path to content. Paths use `/`, are not absolute and contain no `..`.

    Returns:
        Zip bytes; the same mapping always yields the same bytes.

    Raises:
        PackageError: A path is unsafe, there are too many files, or the content is too large.
    """
    # Refuse by the same rules unpack applies, so nothing pack makes is refused on the way back.
    _check_paths(files)
    if sum(len(content) for content in files.values()) > MAX_PACKAGE_BYTES:
        raise PackageError(f"Tool package content exceeds {MAX_PACKAGE_BYTES} bytes.")
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        # Sorted, so mapping insertion order never leaks into the bytes.
        for path in sorted(files):
            info = zipfile.ZipInfo(path, date_time=_FIXED_TIMESTAMP)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = _FIXED_EXTERNAL_ATTR
            info.create_system = _UNIX_CREATE_SYSTEM
            archive.writestr(info, files[path], compresslevel=_FIXED_COMPRESSION_LEVEL)
    return buffer.getvalue()


def unpack(data: bytes) -> Mapping[str, bytes]:
    """Read an archive into memory, refusing anything unsafe.

    Args:
        data: Zip bytes, from the Basket or a model's output; untrusted.

    Returns:
        Relative path to content, in archive order.

    Raises:
        PackageError: Not a zip; an unsafe, duplicate or directory entry; encryption or an
            unsupported compression; or declared or actual content past `MAX_PACKAGE_BYTES`.
    """
    # The compressed size is bounded too: a package larger than the cap compressed is not a tool.
    if len(data) > MAX_PACKAGE_BYTES:
        raise PackageError(f"Tool package is {len(data)} bytes; the limit is {MAX_PACKAGE_BYTES}.")
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise PackageError("Tool package is not a valid zip archive.") from exc
    with archive:
        infos = archive.infolist()
        _check_entries(infos)
        # A corrupt body (a bad CRC, a truncated deflate stream) surfaces from the codec as one of
        # these; each means "not a usable package", so they all become the one typed error.
        try:
            return _read_all(archive, infos)
        except (zipfile.BadZipFile, zlib.error, EOFError, RuntimeError, NotImplementedError) as exc:
            raise PackageError("Tool package holds a corrupt or unreadable entry.") from exc


def sha256_of(data: bytes) -> str:
    """Return the lowercase hex SHA-256 of package bytes: the hash that names the version."""
    return hashlib.sha256(data).hexdigest()


def required_files_present(files: Mapping[str, bytes]) -> tuple[str, ...]:
    """Return the required file names `files` lacks, in a fixed order.

    Args:
        files: An unpacked package.

    Returns:
        Missing names among `tool.py`, `test_tool.py` and `spec.json`; empty when complete.
    """
    return tuple(name for name in _REQUIRED_FILES if name not in files)


class ToolPackage:
    """The package operations as one namespace, the way the design names them.

    A thin holder of static delegates to `pack`, `unpack` and `sha256_of`, plus the limits, so a
    caller may write `ToolPackage.pack(files)` or import the functions directly; both are the
    same code.
    """

    MAX_BYTES = MAX_PACKAGE_BYTES  # Same value as the module constant.

    @staticmethod
    def pack(files: Mapping[str, bytes]) -> bytes:
        """Delegate to `pack`."""
        return pack(files)

    @staticmethod
    def unpack(data: bytes) -> Mapping[str, bytes]:
        """Delegate to `unpack`."""
        return unpack(data)

    @staticmethod
    def sha256_of(data: bytes) -> str:
        """Delegate to `sha256_of`."""
        return sha256_of(data)


def _check_paths(paths: Iterable[str]) -> None:
    """Refuse a path set that is too large or holds an unsafe path."""
    names = list(paths)
    if len(names) > MAX_PACKAGE_FILES:
        raise PackageError(
            f"Tool package holds {len(names)} files; the limit is {MAX_PACKAGE_FILES}."
        )
    for name in names:
        _check_path(name)


def _check_path(name: str) -> None:
    """Refuse one path that could escape the directory it is unpacked under."""
    if not name or len(name) > MAX_PATH_CHARS:
        raise PackageError(f"Tool package path {name[:40]!r} is empty or too long.")
    # A backslash is a separator on Windows, so it could smuggle a `..` past the slash split.
    if "\\" in name or "\x00" in name:
        raise PackageError(f"Tool package path {name!r} holds a backslash or NUL.")
    if name.startswith("/") or (len(name) > 1 and name[1] == ":"):
        raise PackageError(f"Tool package path {name!r} is absolute.")
    # Every segment must be a plain name: no empty (`a//b`), `.` or `..` segment.
    if any(segment in ("", ".", "..") for segment in name.split("/")):
        raise PackageError(f"Tool package path {name!r} holds an empty, '.' or '..' segment.")


def _check_entries(infos: list[zipfile.ZipInfo]) -> None:
    """Refuse an archive with unsafe, duplicate, encrypted or oversized entries."""
    _check_paths(info.filename for info in infos)
    seen: set[str] = set()
    declared = 0
    for info in infos:
        # A duplicate would let a later entry silently replace an earlier, reviewed one.
        if info.filename in seen:
            raise PackageError(f"Tool package holds {info.filename!r} twice.")
        seen.add(info.filename)
        if info.flag_bits & 0x1:
            raise PackageError(f"Tool package entry {info.filename!r} is encrypted.")
        if info.compress_type not in _ALLOWED_COMPRESSION:
            raise PackageError(f"Tool package entry {info.filename!r} uses an unsupported codec.")
        declared += info.file_size
    # The declared sizes are the header's claim; the read below checks the claim against reality.
    if declared > MAX_PACKAGE_BYTES:
        raise PackageError(f"Tool package declares {declared} bytes; limit {MAX_PACKAGE_BYTES}.")


def _read_all(archive: zipfile.ZipFile, infos: list[zipfile.ZipInfo]) -> dict[str, bytes]:
    """Read every entry, bounding the bytes actually produced rather than trusting headers."""
    files: dict[str, bytes] = {}
    total = 0
    for info in infos:
        # Read one byte past the remaining budget: seeing it proves the entry overran the cap.
        with archive.open(info) as entry:
            content = entry.read(MAX_PACKAGE_BYTES - total + 1)
        total += len(content)
        if total > MAX_PACKAGE_BYTES:
            raise PackageError(f"Tool package inflates past {MAX_PACKAGE_BYTES} bytes.")
        files[info.filename] = content
    return files
