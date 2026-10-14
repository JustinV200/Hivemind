"""Tests for hivemind.wardens.watch.probe.system: parsing what the probe's script printed.

Fits into the Hive:
    Mirrors src/hivemind/wardens/watch/probe/system.py (codingrules section 3). The fixtures are
    outputs recorded from a real Linux machine (trimmed), so the parsers are held to what the
    kernel prints rather than to what a test author imagined; hypothesis then feeds them text
    that is nothing like it.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.wardens.watch.probe.system for the module under test.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from builders.watch import stat_line
from hypothesis import given
from hypothesis import strategies as st

from hivemind.wardens.watch.probe.system import (
    DEFAULT_PAGE_SIZE,
    CpuBaseline,
    KernelFacts,
    RawProcess,
    parse_kernel,
    parse_processes,
    parse_resources,
    process_samples,
    split_sections,
)
from waggle.messages.swarm.watch import MAX_INTERFACE_CHARS, MAX_NETWORK_INTERFACES

_MARK = "@@feedc0de00112233"

# Recorded from a Linux 6.x machine with four cores (long lines trimmed).
_STAT = (
    "cpu  1222800 0 155215 8865284 19396 0 37095 33022 0 0\n"
    "cpu0 284845 0 39744 2231892 5862 0 17477 8455 0 0\n"
    "cpu1 319757 0 40243 2201152 5148 0 9703 8402 0 0\n"
    "cpu2 308236 0 37754 2217341 4894 0 4462 8028 0 0\n"
    "cpu3 309960 0 37472 2214898 3490 0 5451 8137 0 0\n"
    "ctxt 36822236\nbtime 1791919661\nprocesses 407274\n"
)
_MEMINFO = (
    "MemTotal:       16479424 kB\nMemFree:         7698576 kB\n"
    "MemAvailable:   15010340 kB\nBuffers:          601776 kB\nDirectMap1G:    16777216 kB\n"
)
_NETDEV = (
    "Inter-|   Receive                                                |  Transmit\n"
    " face |bytes    packets errs drop fifo frame compressed multicast|bytes    packets errs drop\n"
    "    lo: 1144140370  346210    0    0    0     0          0         0 1144140370  346210    0\n"
    "  eth0: 1255656729  839032    0    6    0     0          0         0 429130355  794514    0\n"
    "docker0:  443410    1836    0    0    0     0          0         0 18571757    1980    0\n"
)
_DF = (
    "Filesystem     1024-blocks     Used Available Capacity Mounted on\n"
    "/dev/vda         264212084 15447636  25395976      38% /\n"
)
_PROCS = (
    "1 (process_api) S 0 0 0 0 -1 4194560 2808 7838086 0 976 456 1270 46521 6312 20 0 8 0 39 "
    "25436160 1350 18446744073709551615 1 1 0 0 0 0 0 4096 1088 0 0 0 17 3 0 0 0 0 0\n"
    "10 (kworker/0:0H-events_highpri) I 2 0 0 0 -1 69238880 0 0 0 0 0 0 0 0 0 -20 1 0 40 0 0 "
    "18446744073709551615 0 0 0 0 0 0 0 2147483647 0 1 0 0 17 0 0 0 0 0 0\n"
    "1045 (dockerd) S 1 1044 1043 0 -1 4194560 57454 1367728 912 142 4645 1168 4583 1503 20 0 14 "
    "0 18817 2447085568 30833 18446744073709551615 93990409707520 93990450833053 0\n"
    "1078 (containerd) S 1045 1078 1078 0 -1 4194304 127595 75712 355 98 5683 3994 39 78 20 0 13 "
    "0 18870 2312564736 14643 18446744073709551615 94071573618688 94071591536413 0\n"
)
_FACTS = KernelFacts(cores=4, boot_epoch=1_791_919_661.0, uptime_s=25_856.95, page_size=4_096)


def _sections() -> dict[str, str]:
    """The recorded sections the kernel and resources parsers read."""
    return {
        "stat": _STAT,
        "uptime": "25856.95 88652.84\n",
        "pagesize": "4096\n",
        "loadavg": "1.45 0.97 0.81 3/153 21806\n",
        "meminfo": _MEMINFO,
        "netdev": _NETDEV,
        "df": _DF,
    }


def _process(pid: int, ticks: int, start: int = 100, name: str = "p") -> RawProcess:
    """A RawProcess with `ticks` of CPU time used, started `start` ticks after boot."""
    return RawProcess(pid=pid, name=name, cpu_ticks=ticks, start_ticks=start, rss_bytes=4_096)


# ──────────────────────────────────────────────────────────────────────────────
# split_sections
# ──────────────────────────────────────────────────────────────────────────────


def test_split_sections_reads_each_section_under_its_marker() -> None:
    output = f"\n{_MARK} stat\ncpu 1\ncpu0 2\n\n{_MARK} uptime\n12.5 3.0\n"

    sections = split_sections(output, _MARK)

    assert sections["stat"].splitlines()[:2] == ["cpu 1", "cpu0 2"]
    assert sections["uptime"].strip() == "12.5 3.0"


def test_a_line_that_only_looks_like_a_marker_opens_no_section() -> None:
    # A file name or log line can print anything, but not the run's random mark.
    output = f"{_MARK} stat\nreal\n@@feedc0de00000000 stat\nforged\n@@ uptime\nforged too\n"

    sections = split_sections(output, _MARK)

    assert set(sections) == {"stat"}
    assert "forged" in sections["stat"]  # It stays inside the section it was printed in.


def test_text_before_the_first_marker_is_dropped() -> None:
    assert split_sections(f"noise\n{_MARK} stat\nx", _MARK) == {"stat": "x"}


def test_split_sections_splits_on_the_newline_alone() -> None:
    odd = "a" + chr(0x2028) + "b\x0cc\rd\x1ce"  # Separators str.splitlines would cut at.
    output = f"{_MARK} roots\n{odd}\n"

    assert split_sections(output, _MARK)["roots"].startswith(odd)


@given(st.text())
def test_split_sections_never_raises_and_never_invents_a_section(text: str) -> None:
    assert split_sections(text, _MARK) == {} or _MARK + " " in text


# ──────────────────────────────────────────────────────────────────────────────
# parse_kernel
# ──────────────────────────────────────────────────────────────────────────────


def test_parse_kernel_reads_cores_boot_time_uptime_and_page_size() -> None:
    assert parse_kernel(_sections()) == _FACTS


def test_parse_kernel_does_not_count_the_cpu_total_line_as_a_core() -> None:
    facts = parse_kernel({**_sections(), "stat": "cpu 1 1\ncpu0 1\nbtime 5\n"})

    assert facts is not None
    assert facts.cores == 1


@pytest.mark.parametrize("missing", ["stat", "uptime"])
def test_parse_kernel_is_none_without_the_clock_or_the_cpus(missing: str) -> None:
    sections = _sections()
    del sections[missing]

    assert parse_kernel(sections) is None


def test_parse_kernel_is_none_without_a_boot_time() -> None:
    assert parse_kernel({**_sections(), "stat": "cpu0 1\n"}) is None


@pytest.mark.parametrize("page", ["", "0", "-4", "many"])
def test_parse_kernel_falls_back_to_the_usual_page_size(page: str) -> None:
    facts = parse_kernel({**_sections(), "pagesize": page})

    assert facts is not None
    assert facts.page_size == DEFAULT_PAGE_SIZE


# ──────────────────────────────────────────────────────────────────────────────
# parse_processes
# ──────────────────────────────────────────────────────────────────────────────


def test_parse_processes_reads_the_recorded_stat_lines() -> None:
    found = {p.pid: p for p in parse_processes(_PROCS, _FACTS)}

    assert set(found) == {1, 10, 1045, 1078}
    assert found[1] == RawProcess(1, "process_api", 456 + 1270, 39, 1350 * 4096)
    assert found[10].name == "kworker/0:0H-events_highpri"
    assert (found[1045].cpu_ticks, found[1045].start_ticks) == (4645 + 1168, 18817)
    assert found[1078].rss_bytes == 14643 * 4096


def test_a_name_with_spaces_and_parentheses_is_read_whole() -> None:
    (process,) = parse_processes(stat_line(77, "a (b) c", (5, 6), 90, 12), _FACTS)

    assert (process.pid, process.name, process.cpu_ticks, process.start_ticks) == (
        77,
        "a (b) c",
        11,
        90,
    )
    assert process.rss_bytes == 12 * 4096


@pytest.mark.parametrize(
    "line",
    [
        "",
        "garbage",
        "12 (short) S 1 2",
        "x (name) S " + "0 " * 30,
        "5 name S " + "0 " * 30,
        "(a) S 1",
    ],
)
def test_a_malformed_line_is_skipped_not_raised(line: str) -> None:
    assert parse_processes(line, _FACTS) == []


def test_a_name_holding_a_newline_costs_that_process_only() -> None:
    text = "5 (split\nname) S " + "0 " * 30 + "\n" + _PROCS

    pids = [p.pid for p in parse_processes(text, _FACTS)]

    assert pids == [1, 10, 1045, 1078]


@given(st.text())
def test_parse_processes_never_raises_whatever_it_is_given(text: str) -> None:
    assert isinstance(parse_processes(text, _FACTS), list)


# ──────────────────────────────────────────────────────────────────────────────
# CpuBaseline and process_samples
# ──────────────────────────────────────────────────────────────────────────────


def test_the_first_sample_measures_a_share_over_the_processs_whole_life() -> None:
    # Started at tick 100 (1 s after boot); the machine is 1,001 s old; 400 ticks = 4 s of CPU.
    facts = KernelFacts(cores=4, boot_epoch=1_000_000.0, uptime_s=1_001.0, page_size=4_096)

    samples, cut = process_samples([_process(5, 400)], facts, CpuBaseline(), 10)

    assert cut is False
    assert samples[0].cpu_share == pytest.approx(4.0 / 1_000.0 / 4)
    assert samples[0].started_at == datetime.fromtimestamp(1_000_001.0, UTC)


def test_a_later_sample_measures_the_share_since_the_previous_one() -> None:
    baseline = CpuBaseline()
    first = KernelFacts(cores=4, boot_epoch=1_000_000.0, uptime_s=1_000.0, page_size=4_096)
    second = KernelFacts(cores=4, boot_epoch=1_000_000.0, uptime_s=1_010.0, page_size=4_096)
    process_samples([_process(5, 1_000)], first, baseline, 10)

    # Ten seconds later it has used 20 more CPU seconds: two of four cores, a share of 0.5.
    (sample,) = process_samples([_process(5, 3_000)], second, baseline, 10)[0]

    assert sample.cpu_share == pytest.approx(0.5)


def test_a_reused_pid_is_a_new_process_with_no_baseline() -> None:
    baseline = CpuBaseline()
    first = KernelFacts(cores=1, boot_epoch=1_000_000.0, uptime_s=1_000.0, page_size=4_096)
    second = KernelFacts(cores=1, boot_epoch=1_000_000.0, uptime_s=1_010.0, page_size=4_096)
    process_samples([_process(5, 90_000, start=100)], first, baseline, 10)

    # Same pid, started later: the old process's 900 s of CPU must not be subtracted from it.
    (sample,) = process_samples([_process(5, 500, start=100_500)], second, baseline, 10)[0]

    assert sample.cpu_share == pytest.approx(5.0 / 5.0)  # 5 CPU s over a 5 s life, one core.


def test_a_process_that_is_gone_is_forgotten_by_the_baseline() -> None:
    baseline = CpuBaseline()
    facts = KernelFacts(cores=1, boot_epoch=1_000_000.0, uptime_s=1_000.0, page_size=4_096)
    process_samples([_process(5, 100), _process(6, 100)], facts, baseline, 10)

    baseline.shares([_process(6, 100)], facts)

    assert baseline.shares([_process(5, 100)], facts) == [pytest.approx(100 / 100 / 999)]


def test_samples_are_busiest_first_and_a_cut_is_reported() -> None:
    facts = KernelFacts(cores=1, boot_epoch=1_000_000.0, uptime_s=1_000.0, page_size=4_096)
    processes = [_process(1, 100), _process(2, 90_000), _process(3, 5_000)]

    samples, cut = process_samples(processes, facts, CpuBaseline(), 2)

    assert [sample.pid for sample in samples] == [2, 3]
    assert cut is True


def test_a_share_is_clamped_to_the_wire_bound() -> None:
    facts = KernelFacts(cores=1, boot_epoch=1_000_000.0, uptime_s=10.0, page_size=4_096)

    (sample,) = process_samples([_process(1, 10_000_000, start=0)], facts, CpuBaseline(), 5)[0]

    assert sample.cpu_share == 1.0


def test_a_process_that_started_in_the_instant_of_the_read_has_a_zero_share() -> None:
    facts = KernelFacts(cores=2, boot_epoch=1_000_000.0, uptime_s=10.0, page_size=4_096)

    (sample,) = process_samples([_process(1, 3, start=1_000)], facts, CpuBaseline(), 5)[0]

    assert sample.cpu_share == 0.0


# ──────────────────────────────────────────────────────────────────────────────
# parse_resources
# ──────────────────────────────────────────────────────────────────────────────


def test_parse_resources_reads_the_recorded_sections() -> None:
    resources = parse_resources(_sections(), _FACTS)

    assert resources is not None
    assert resources.cpu_load == pytest.approx(1.45 / 4)
    assert resources.memory_bytes == 16_479_424 * 1024
    assert resources.memory_free_bytes == 15_010_340 * 1024
    assert (resources.disk_bytes, resources.disk_free_bytes) == (
        264_212_084 * 1024,
        25_395_976 * 1024,
    )
    assert resources.uptime_s == 25_856.95
    assert [(c.interface, c.rx_bytes, c.tx_bytes) for c in resources.network] == [
        ("docker0", 443_410, 18_571_757),
        ("eth0", 1_255_656_729, 429_130_355),
        ("lo", 1_144_140_370, 1_144_140_370),
    ]


def test_parse_resources_is_none_when_neither_memory_nor_disk_was_printed() -> None:
    assert parse_resources({"loadavg": "1 1 1", "netdev": _NETDEV}, _FACTS) is None


def test_parse_resources_keeps_what_was_printed_when_a_part_is_missing() -> None:
    resources = parse_resources({**_sections(), "df": "", "loadavg": ""}, _FACTS)

    assert resources is not None
    assert (resources.disk_bytes, resources.disk_free_bytes, resources.cpu_load) == (0, 0, 0.0)


def test_free_memory_never_exceeds_total_memory() -> None:
    odd = "MemTotal: 100 kB\nMemAvailable: 900 kB\n"

    resources = parse_resources({**_sections(), "meminfo": odd}, _FACTS)

    assert resources is not None
    assert resources.memory_free_bytes == resources.memory_bytes


def test_an_old_kernel_with_only_memfree_still_reports_free_memory() -> None:
    old = "MemTotal: 1000 kB\nMemFree: 400 kB\n"

    resources = parse_resources({**_sections(), "meminfo": old}, _FACTS)

    assert resources is not None
    assert resources.memory_free_bytes == 400 * 1024


def test_interfaces_are_unique_cut_to_their_bound_and_limited_in_number() -> None:
    long_name = "e" * (MAX_INTERFACE_CHARS + 10)
    rows = [
        f"if{n:03d}: 1 1 0 0 0 0 0 0 2 1 0 0 0 0 0 0" for n in range(MAX_NETWORK_INTERFACES + 5)
    ]
    text = "\n".join([f"{long_name}: 1 1 0 0 0 0 0 0 2 1 0 0 0 0 0 0", *rows, *rows[:1]])

    resources = parse_resources({**_sections(), "netdev": text}, _FACTS)

    assert resources is not None
    names = [counter.interface for counter in resources.network]
    assert len(names) == len(set(names)) == MAX_NETWORK_INTERFACES
    assert names == sorted(names)
    assert all(len(name) <= MAX_INTERFACE_CHARS for name in names)


@given(
    st.fixed_dictionaries({}, optional={name: st.text() for name in ("meminfo", "df", "netdev")})
)
def test_parse_resources_never_raises_whatever_it_is_given(sections: dict[str, str]) -> None:
    result = parse_resources({**sections, "loadavg": "x", "uptime": "y"}, _FACTS)

    assert result is None or result.memory_free_bytes <= result.memory_bytes
