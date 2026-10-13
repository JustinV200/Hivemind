"""Test the system half of watch mode: the process list and the resource snapshot.

A Warden (the per-Cell supervisor) watching an idle Real Cell (a device the Hive borrows) asks its
Pollen Packet (the thin gateway program on the device) what the device looks like (ADR-0054). The
process list comes busiest first, with each process's name (never its arguments, which can carry
secrets), its share of the whole machine's CPU and its resident memory; the resource snapshot
holds load, memory, disk, uptime and the network counters. Both read the platform through psutil
and run no command, and a figure the platform will not give is zero, never a guess. These tests
read the real host once, and feed the collectors scripted psutil answers to pin down the clamps,
the ordering, the cuts and the missing figures a real host rarely shows.

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Tests pollen.executors.observe.system.

Key invariants:
    - A share is between 0 and 1 and a missing figure is zero, so every sample fits the wire.
    - Every list is cut at the wire bound, busiest or first-named first, and says so.

See Also:
    - docs/waggle/gateway-contract.md section 9.4 and ADR-0054.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace

import psutil
import pytest

from pollen.executors.observe.system import collect_processes, collect_resources, prime_cpu
from waggle.messages.swarm.watch import (
    MAX_INTERFACE_CHARS,
    MAX_NETWORK_INTERFACES,
    MAX_PROCESS_NAME_CHARS,
)

EPOCH_S = 1_700_000_000.0  # A creation time for a scripted process.


def _process(**info: object) -> SimpleNamespace:
    """A scripted psutil process whose ``info`` holds the attributes the collector asks for."""
    values: dict[str, object] = {
        "pid": 1,
        "name": "scripted",
        "cpu_percent": 0.0,
        "memory_info": SimpleNamespace(rss=10),
        "create_time": EPOCH_S,
    }
    values.update(info)
    return SimpleNamespace(info=values)


def _script(monkeypatch: pytest.MonkeyPatch, processes: list[SimpleNamespace], cores: int) -> None:
    """Make psutil report ``processes`` on a machine with ``cores`` logical cores."""

    def process_iter(_attrs: object = None) -> Iterator[SimpleNamespace]:
        return iter(processes)

    monkeypatch.setattr(psutil, "process_iter", process_iter)
    monkeypatch.setattr(psutil, "cpu_count", lambda logical=True: cores)


def test_priming_the_cpu_baseline_reads_every_process_once() -> None:
    prime_cpu()


def test_the_real_process_list_includes_this_process() -> None:
    samples, cut = collect_processes(10_000)
    assert os.getpid() in {s.pid for s in samples} and not cut
    assert all(0.0 <= s.cpu_share <= 1.0 and s.rss_bytes >= 0 for s in samples)


def test_the_busiest_processes_come_first_and_ties_go_to_the_larger(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _script(
        monkeypatch,
        [
            _process(pid=1, cpu_percent=10.0, memory_info=SimpleNamespace(rss=1)),
            _process(pid=2, cpu_percent=50.0, memory_info=SimpleNamespace(rss=1)),
            _process(pid=3, cpu_percent=10.0, memory_info=SimpleNamespace(rss=9)),
        ],
        cores=2,
    )
    samples, cut = collect_processes(10)
    assert [s.pid for s in samples] == [2, 3, 1] and not cut
    assert samples[0].cpu_share == pytest.approx(0.25)  # 50 % of one core on two cores.


def test_a_list_longer_than_asked_is_cut_and_says_so(monkeypatch: pytest.MonkeyPatch) -> None:
    _script(monkeypatch, [_process(pid=n, cpu_percent=float(n)) for n in range(1, 5)], cores=1)
    samples, cut = collect_processes(2)
    assert [s.pid for s in samples] == [4, 3] and cut  # The least busy are the ones lost.
    assert collect_processes(4)[1] is False


def test_a_share_is_kept_between_zero_and_one(monkeypatch: pytest.MonkeyPatch) -> None:
    _script(
        monkeypatch,
        [_process(pid=1, cpu_percent=900.0), _process(pid=2, cpu_percent=-5.0)],
        cores=1,
    )
    shares = {s.pid: s.cpu_share for s in collect_processes(10)[0]}
    assert shares == {1: 1.0, 2: 0.0}


def test_a_figure_the_platform_will_not_give_is_zero_or_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _script(
        monkeypatch,
        [_process(name=None, cpu_percent=None, memory_info=None, create_time=None)],
        cores=0,  # psutil may not know the core count either.
    )
    (sample,), _ = collect_processes(10)
    assert (sample.name, sample.cpu_share, sample.rss_bytes, sample.started_at) == (
        "",
        0.0,
        0,
        None,
    )


def test_a_long_process_name_is_cut_at_the_wire_bound(monkeypatch: pytest.MonkeyPatch) -> None:
    _script(monkeypatch, [_process(name="n" * (MAX_PROCESS_NAME_CHARS + 50))], cores=1)
    assert len(collect_processes(10)[0][0].name) == MAX_PROCESS_NAME_CHARS


def test_the_real_resource_snapshot_describes_this_host(tmp_path: Path) -> None:
    sample = collect_resources(tmp_path)
    assert sample.memory_bytes > 0 and sample.disk_bytes > 0 and sample.uptime_s > 0
    assert sample.memory_free_bytes <= sample.memory_bytes
    assert sample.disk_free_bytes <= sample.disk_bytes


def _counters(**nics: tuple[int, int]) -> dict[str, SimpleNamespace]:
    """Scripted per-interface counters as ``name=(received, sent)``."""
    return {n: SimpleNamespace(bytes_recv=rx, bytes_sent=tx) for n, (rx, tx) in nics.items()}


def test_network_counters_are_sorted_by_name_and_skip_a_nameless_interface(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    scripted = {
        **_counters(lo=(1, 2), eth0=(30, 40)),
        "": SimpleNamespace(bytes_recv=9, bytes_sent=9),
    }
    monkeypatch.setattr(psutil, "net_io_counters", lambda pernic=False: scripted)
    sample = collect_resources(tmp_path)
    assert [(n.interface, n.rx_bytes, n.tx_bytes) for n in sample.network] == [
        ("eth0", 30, 40),
        ("lo", 1, 2),
    ]


def test_interface_names_that_collide_once_cut_keep_only_the_first(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    long = "x" * MAX_INTERFACE_CHARS
    scripted = _counters(**{long + "a": (1, 1), long + "b": (2, 2)})
    monkeypatch.setattr(psutil, "net_io_counters", lambda pernic=False: scripted)
    sample = collect_resources(tmp_path)
    assert [(n.interface, n.rx_bytes) for n in sample.network] == [(long, 1)]


def test_more_interfaces_than_the_wire_allows_are_cut(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    scripted = _counters(**{f"if{n:03d}": (n, n) for n in range(MAX_NETWORK_INTERFACES + 8)})
    monkeypatch.setattr(psutil, "net_io_counters", lambda pernic=False: scripted)
    assert len(collect_resources(tmp_path).network) == MAX_NETWORK_INTERFACES


def test_a_host_with_no_network_counters_reports_none(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(psutil, "net_io_counters", lambda pernic=False: None)
    assert collect_resources(tmp_path).network == ()
