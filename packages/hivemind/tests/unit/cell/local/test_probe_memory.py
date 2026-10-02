"""Unit tests for hivemind.cell.local.probe.linux_memory_bytes, over fake /proc and cgroup files.

Fits into the Hive:
    Mirrors src/hivemind/cell/local/probe.py, split from test_probe.py by feature (codingrules
    5.1): free memory on Linux, which phase 6 handoff 4.3 found read as MemFree.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.cell.local.probe for the module under test.
"""

from __future__ import annotations

from pathlib import Path

from hivemind.cell.local.probe import linux_memory_bytes

_MIB = 1024**2
_GIB = 1024**3


def _meminfo(directory: Path, total_kib: int, free_kib: int, available_kib: int | None) -> Path:
    """Write a fake /proc/meminfo in the kernel's own layout; no MemAvailable when it is None."""
    lines = [f"MemTotal:       {total_kib} kB", f"MemFree:        {free_kib} kB"]
    if available_kib is not None:
        lines.append(f"MemAvailable:   {available_kib} kB")
    lines.append("Buffers:          123 kB")
    path = directory / "meminfo"
    path.write_text("\n".join(lines) + "\n", encoding="ascii")
    return path


def _cgroup_v2(directory: Path, limit: str, current: int, inactive_file: int) -> Path:
    """Write a fake cgroup v2 root: memory.max, memory.current and memory.stat."""
    root = directory / "cgroup"
    root.mkdir()
    (root / "memory.max").write_text(f"{limit}\n", encoding="ascii")
    (root / "memory.current").write_text(f"{current}\n", encoding="ascii")
    stat = f"anon 1000\nfile 2000\ninactive_file {inactive_file}\nactive_file 5\n"
    (root / "memory.stat").write_text(stat, encoding="ascii")
    return root


def _cgroup_v1(directory: Path, limit: int, usage: int, inactive_file: int) -> Path:
    """Write a fake cgroup v1 root: the memory controller's limit, usage and stat files."""
    root = directory / "cgroup"
    (root / "memory").mkdir(parents=True)
    (root / "memory" / "memory.limit_in_bytes").write_text(f"{limit}\n", encoding="ascii")
    (root / "memory" / "memory.usage_in_bytes").write_text(f"{usage}\n", encoding="ascii")
    stat = f"cache 10\ntotal_inactive_file {inactive_file}\n"
    (root / "memory" / "memory.stat").write_text(stat, encoding="ascii")
    return root


def test_linux_memory_reads_mem_available_not_mem_free_on_a_host(tmp_path: Path) -> None:
    # The sandbox the handoff describes: 717 MiB free, 15 GiB available once cache is counted.
    meminfo = _meminfo(tmp_path, 16 * _GIB // 1024, 717 * _MIB // 1024, 15 * _GIB // 1024)

    total, available = linux_memory_bytes(meminfo, tmp_path / "no-cgroup")

    assert (total, available) == (16 * _GIB, 15 * _GIB)


def test_linux_memory_falls_back_to_mem_free_on_a_kernel_without_mem_available(
    tmp_path: Path,
) -> None:
    meminfo = _meminfo(tmp_path, 4 * _GIB // 1024, 1 * _GIB // 1024, None)

    assert linux_memory_bytes(meminfo, tmp_path / "no-cgroup") == (4 * _GIB, 1 * _GIB)


def test_linux_memory_is_unknown_without_proc_meminfo(tmp_path: Path) -> None:
    assert linux_memory_bytes(tmp_path / "missing", tmp_path / "no-cgroup") == (0, 0)


def test_linux_memory_reports_a_v2_containers_own_limit_and_working_set(tmp_path: Path) -> None:
    # A 1 GiB container on a 16 GiB host: 400 MiB charged, 100 MiB of it reclaimable cache.
    meminfo = _meminfo(tmp_path, 16 * _GIB // 1024, 1 * _GIB // 1024, 12 * _GIB // 1024)
    root = _cgroup_v2(tmp_path, str(_GIB), 400 * _MIB, 100 * _MIB)

    total, available = linux_memory_bytes(meminfo, root)

    assert (total, available) == (_GIB, _GIB - 300 * _MIB)


def test_linux_memory_treats_v2_max_as_unlimited_and_reads_the_host(tmp_path: Path) -> None:
    meminfo = _meminfo(tmp_path, 8 * _GIB // 1024, 1 * _GIB // 1024, 6 * _GIB // 1024)
    root = _cgroup_v2(tmp_path, "max", 400 * _MIB, 0)

    assert linux_memory_bytes(meminfo, root) == (8 * _GIB, 6 * _GIB)


def test_linux_memory_reads_a_v1_containers_limit(tmp_path: Path) -> None:
    meminfo = _meminfo(tmp_path, 16 * _GIB // 1024, 1 * _GIB // 1024, 12 * _GIB // 1024)
    root = _cgroup_v1(tmp_path, 2 * _GIB, 1 * _GIB, 256 * _MIB)

    assert linux_memory_bytes(meminfo, root) == (2 * _GIB, _GIB + 256 * _MIB)


def test_linux_memory_ignores_a_v1_limit_past_the_hosts_own_memory(tmp_path: Path) -> None:
    # The v1 root cgroup's "no limit" is a huge page-counter maximum, not the word "max".
    meminfo = _meminfo(tmp_path, 8 * _GIB // 1024, 1 * _GIB // 1024, 6 * _GIB // 1024)
    root = _cgroup_v1(tmp_path, 9223372036854771712, 3 * _GIB, 0)

    assert linux_memory_bytes(meminfo, root) == (8 * _GIB, 6 * _GIB)


def test_linux_memory_never_reports_more_available_than_the_host_has(tmp_path: Path) -> None:
    # A 4 GiB container on a host with only 1 GiB available: the host's figure binds.
    meminfo = _meminfo(tmp_path, 16 * _GIB // 1024, 512 * _MIB // 1024, 1 * _GIB // 1024)
    root = _cgroup_v2(tmp_path, str(4 * _GIB), 100 * _MIB, 0)

    assert linux_memory_bytes(meminfo, root) == (4 * _GIB, 1 * _GIB)
