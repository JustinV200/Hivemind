"""Tests for waggle.messages.swarm.watch: Observe, Observation, their value models and enums.

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Pins the two enums and the constants' agreement
    with each other, and for every class construction, the JSON round trip, the bounds and the
    id kinds, and every validator spec section 8.10 names in both directions: Observe's kinds
    unique and its roots and cursor present exactly where the kinds need them, a ResourceSample's
    free figures within their totals and its interfaces unique, a FileChange's deleted file with
    no size. Finally builds a maximal Observation (every list full, every string at its bound)
    and checks its signed frame stays well under MAX_FRAME_BYTES. The family's EXAMPLES live in
    test_enrolment.py.

Key invariants:
    - None: this module holds tests only.

See Also:
    - waggle.messages.swarm.watch for the module under test.
    - docs/waggle/spec.md section 8.10 for the fields, bounds and validators pinned here.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from waggle.clock import FakeClock
from waggle.codec import MAX_FRAME_BYTES, Codec
from waggle.envelope import Envelope
from waggle.ids import IdKind, new_id, new_message_id
from waggle.messages.base import MAX_PATH_CHARS, MAX_REASON_CHARS
from waggle.messages.swarm.watch import (
    DEFAULT_OBSERVE_ITEMS,
    MAX_FILE_CHANGES,
    MAX_INTERFACE_CHARS,
    MAX_LOG_LINE_CHARS,
    MAX_LOG_LINES,
    MAX_NETWORK_INTERFACES,
    MAX_OBSERVE_ITEMS,
    MAX_OBSERVE_KINDS,
    MAX_OBSERVE_ROOTS,
    MAX_OBSERVED_PATH_CHARS,
    MAX_PROCESS_NAME_CHARS,
    MAX_PROCESSES,
    FileChange,
    FileChangeKind,
    LogLine,
    NetworkCounter,
    Observation,
    ObservationKind,
    Observe,
    ProcessSample,
    ResourceSample,
)

CLOCK = FakeClock()
NOW = CLOCK.now()
DEVICE_ID = new_id(IdKind.DEVICE, CLOCK)
CELL_ID = new_id(IdKind.CELL, CLOCK)
GIB = 1_073_741_824  # One gibibyte, so the figures read as a real host.
# The kinds that read files, and the others; roots belong to the first group alone.
ROOTED = (ObservationKind.LOGS, ObservationKind.FILE_CHANGES)
UNROOTED = (ObservationKind.PROCESS_LIST, ObservationKind.RESOURCE_USE)


def _observe(**overrides: object) -> Observe:
    """A request for the process list and the logs under /var/log, then ``overrides``."""
    fields: dict[str, object] = {
        "device_id": DEVICE_ID,
        "kinds": (ObservationKind.PROCESS_LIST, ObservationKind.LOGS),
        "roots": ("/var/log",),
        "since_at": None,
        "reason": "A scheduled sample of an idle device.",
    }
    return Observe.model_validate({**fields, **overrides})


def _resources(**overrides: object) -> ResourceSample:
    """A host with half its memory and a fifth of its disk free, then ``overrides``."""
    fields: dict[str, object] = {
        "cpu_load": 0.4,
        "memory_bytes": 32 * GIB,
        "memory_free_bytes": 16 * GIB,
        "disk_bytes": 512 * GIB,
        "disk_free_bytes": 100 * GIB,
        "uptime_s": 86_400.0,
        "network": ({"interface": "eth0", "rx_bytes": GIB, "tx_bytes": GIB // 4},),
    }
    return ResourceSample.model_validate({**fields, **overrides})


def _counters(count: int) -> list[dict[str, object]]:
    """``count`` NetworkCounter dicts with distinct interface names."""
    return [{"interface": f"if{index}", "rx_bytes": 1, "tx_bytes": 2} for index in range(count)]


def _observation(**overrides: object) -> Observation:
    """An empty answer: nothing asked for, nothing found, then ``overrides``."""
    fields: dict[str, object] = {
        "device_id": DEVICE_ID,
        "observed_at": NOW,
        "processes": (),
        "resources": None,
        "log_lines": (),
        "file_changes": (),
        "is_truncated": False,
        "reason": "The packet answered with nothing to report.",
    }
    return Observation.model_validate({**fields, **overrides})


def _process(**overrides: object) -> ProcessSample:
    """A browser using a third of the device's CPU, then ``overrides``."""
    fields: dict[str, object] = {
        "pid": 812,
        "name": "firefox",
        "cpu_share": 0.31,
        "rss_bytes": GIB,
        "started_at": NOW,
    }
    return ProcessSample.model_validate({**fields, **overrides})


def _change(**overrides: object) -> FileChange:
    """A modified log file, then ``overrides``."""
    fields: dict[str, object] = {
        "path": "/var/log/syslog",
        "change": FileChangeKind.MODIFIED,
        "at": NOW,
        "size_bytes": 4_096,
    }
    return FileChange.model_validate({**fields, **overrides})


# ──────────────────────────────────────────────────────────────────────────────
# Enums and constants
# ──────────────────────────────────────────────────────────────────────────────


def test_watch_enums_list_exactly_the_spec_members_with_values_equal_to_names() -> None:
    assert [member.value for member in ObservationKind] == [
        "PROCESS_LIST",
        "RESOURCE_USE",
        "LOGS",
        "FILE_CHANGES",
    ]
    assert [member.value for member in FileChangeKind] == ["CREATED", "MODIFIED", "DELETED"]
    assert all(m.name == m.value for enum in (ObservationKind, FileChangeKind) for m in enum)


def test_the_bounds_agree_with_each_other() -> None:
    # A request may not ask for more than an answer may hold, and one kind at most once.
    assert len(ObservationKind) == MAX_OBSERVE_KINDS
    assert MAX_PROCESSES == MAX_FILE_CHANGES == MAX_OBSERVE_ITEMS
    assert 1 <= DEFAULT_OBSERVE_ITEMS <= MAX_OBSERVE_ITEMS
    assert MAX_OBSERVED_PATH_CHARS < MAX_PATH_CHARS


# ──────────────────────────────────────────────────────────────────────────────
# Observe
# ──────────────────────────────────────────────────────────────────────────────


def test_observe_round_trips_and_defaults_the_page_size() -> None:
    request = _observe()
    everything = _observe(
        kinds=tuple(ObservationKind),
        roots=("/var/log", "/home/user/Documents"),
        since_at=NOW,
        max_items=MAX_OBSERVE_ITEMS,
    )
    resources_only = _observe(kinds=(ObservationKind.RESOURCE_USE,), roots=())

    for message in (request, everything, resources_only):
        assert Observe.model_validate(message.model_dump(mode="json")) == message
    assert request.max_items == DEFAULT_OBSERVE_ITEMS


@pytest.mark.parametrize("kind", ROOTED)
def test_observe_needs_a_root_for_each_kind_that_reads_files(kind: ObservationKind) -> None:
    assert _observe(kinds=(kind,), roots=("/srv",)).roots == ("/srv",)
    with pytest.raises(ValidationError, match="roots are required exactly when kinds includes"):
        _observe(kinds=(kind,), roots=())


@pytest.mark.parametrize("kind", UNROOTED)
def test_observe_refuses_roots_for_a_kind_that_reads_no_file(kind: ObservationKind) -> None:
    assert _observe(kinds=(kind,), roots=()).roots == ()
    with pytest.raises(ValidationError, match="roots are required exactly when kinds includes"):
        _observe(kinds=(kind,), roots=("/var/log",))


def test_observe_allows_the_cursor_only_with_file_changes() -> None:
    changes = _observe(kinds=(ObservationKind.FILE_CHANGES,), since_at=NOW)

    assert changes.since_at == NOW
    with pytest.raises(ValidationError, match="since_at is allowed only when kinds includes"):
        _observe(since_at=NOW)


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"kinds": ()}, "at least 1"),
        ({"kinds": (ObservationKind.LOGS, ObservationKind.LOGS)}, "kinds must be unique"),
        ({"kinds": ("SCREEN",)}, "kinds"),
        ({"roots": ("/var/log",) * (MAX_OBSERVE_ROOTS + 1)}, f"at most {MAX_OBSERVE_ROOTS}"),
        ({"roots": ("",)}, "at least 1"),
        # A root is bounded like the paths reported under it, so no root asks for what could
        # never be reported back.
        (
            {"roots": ("/" + "x" * MAX_OBSERVED_PATH_CHARS,)},
            f"at most {MAX_OBSERVED_PATH_CHARS}",
        ),
        ({"since_at": "2026-10-13T12:00:00"}, "timezone"),
        ({"max_items": 0}, "greater than or equal to 1"),
        ({"max_items": MAX_OBSERVE_ITEMS + 1}, f"less than or equal to {MAX_OBSERVE_ITEMS}"),
        ({"reason": "x" * (MAX_REASON_CHARS + 1)}, f"at most {MAX_REASON_CHARS}"),
        ({"device_id": CELL_ID}, "device_"),
        ({"pattern": "*.log"}, "extra"),
    ],
)
def test_observe_validators(changes: dict[str, object], reason: str) -> None:
    with pytest.raises(ValidationError, match=reason):
        _observe(**changes)


def test_observe_accepts_every_bound() -> None:
    roots = ("/" + "x" * (MAX_OBSERVED_PATH_CHARS - 1),) * MAX_OBSERVE_ROOTS

    assert len(_observe(roots=roots).roots) == MAX_OBSERVE_ROOTS
    assert _observe(max_items=1).max_items == 1
    assert _observe(kinds=tuple(ObservationKind)[:MAX_OBSERVE_KINDS])


# ──────────────────────────────────────────────────────────────────────────────
# The value models
# ──────────────────────────────────────────────────────────────────────────────


def test_process_sample_round_trips_and_accepts_its_bounds() -> None:
    sample = _process()
    unknown_start = _process(started_at=None)
    extremes = _process(pid=0, name="n" * MAX_PROCESS_NAME_CHARS, cpu_share=1, rss_bytes=0)

    for item in (sample, unknown_start, extremes):
        assert ProcessSample.model_validate(item.model_dump(mode="json")) == item
    assert _process(cpu_share=0).cpu_share == 0


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"pid": -1}, "greater than or equal to 0"),
        ({"name": "n" * (MAX_PROCESS_NAME_CHARS + 1)}, f"at most {MAX_PROCESS_NAME_CHARS}"),
        ({"cpu_share": -0.1}, "greater than or equal to 0"),
        ({"cpu_share": 1.01}, "less than or equal to 1"),
        ({"rss_bytes": -1}, "greater than or equal to 0"),
        ({"started_at": "2026-10-13T12:00:00"}, "timezone"),
        ({"cmdline": ["firefox", "--token=x"]}, "extra"),
    ],
)
def test_process_sample_validators(changes: dict[str, object], reason: str) -> None:
    with pytest.raises(ValidationError, match=reason):
        _process(**changes)


def test_resource_sample_round_trips_with_no_network_and_full_figures() -> None:
    sample = _resources()
    no_network = _resources(network=())
    full = _resources(memory_free_bytes=32 * GIB, disk_free_bytes=512 * GIB)

    for item in (sample, no_network, full):
        assert ResourceSample.model_validate(item.model_dump(mode="json")) == item
    assert len(_resources(network=tuple(_counters(MAX_NETWORK_INTERFACES))).network) == 32


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"memory_free_bytes": 33 * GIB}, "memory_free_bytes .* more than memory_bytes"),
        ({"disk_free_bytes": 513 * GIB}, "disk_free_bytes .* more than disk_bytes"),
        ({"network": tuple(_counters(MAX_NETWORK_INTERFACES + 1))}, "at most 32"),
        ({"network": tuple(_counters(1)) * 2}, "interface names must be unique"),
        ({"cpu_load": -0.1}, "greater than or equal to 0"),
        ({"memory_bytes": -1, "memory_free_bytes": -1}, "greater than or equal to 0"),
        ({"uptime_s": -1}, "greater than or equal to 0"),
        ({"gpus": ()}, "extra"),
    ],
)
def test_resource_sample_validators(changes: dict[str, object], reason: str) -> None:
    with pytest.raises(ValidationError, match=reason):
        _resources(**changes)


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"interface": ""}, "at least 1"),
        ({"interface": "i" * (MAX_INTERFACE_CHARS + 1)}, f"at most {MAX_INTERFACE_CHARS}"),
        ({"rx_bytes": -1}, "greater than or equal to 0"),
        ({"tx_bytes": -1}, "greater than or equal to 0"),
    ],
)
def test_network_counter_validators(changes: dict[str, object], reason: str) -> None:
    counter = {"interface": "eth0", "rx_bytes": 1, "tx_bytes": 2}
    assert NetworkCounter.model_validate(counter).interface == "eth0"
    with pytest.raises(ValidationError, match=reason):
        NetworkCounter.model_validate({**counter, **changes})


def test_log_line_and_file_change_round_trip_and_bound_their_paths() -> None:
    line = LogLine(path="/var/log/syslog", line="")
    longest = LogLine(path="p" * MAX_OBSERVED_PATH_CHARS, line="l" * MAX_LOG_LINE_CHARS)

    for item in (line, longest):
        assert LogLine.model_validate(item.model_dump(mode="json")) == item
    for bad in (
        {"path": "", "line": "x"},
        {"path": "p" * (MAX_OBSERVED_PATH_CHARS + 1), "line": "x"},
        {"path": "/var/log/syslog", "line": "l" * (MAX_LOG_LINE_CHARS + 1)},
    ):
        with pytest.raises(ValidationError, match=r"at (least|most)"):
            LogLine.model_validate(bad)


@pytest.mark.parametrize("change", [FileChangeKind.CREATED, FileChangeKind.MODIFIED])
def test_file_change_of_a_present_file_carries_a_size_or_none(change: FileChangeKind) -> None:
    sized = _change(change=change)
    unreadable = _change(change=change, size_bytes=None)

    for item in (sized, unreadable):
        assert FileChange.model_validate(item.model_dump(mode="json")) == item
    assert _change(change=change, size_bytes=0).size_bytes == 0


def test_file_change_of_a_deleted_file_has_no_size() -> None:
    deleted = _change(change=FileChangeKind.DELETED, size_bytes=None)

    assert FileChange.model_validate(deleted.model_dump(mode="json")) == deleted
    with pytest.raises(ValidationError, match="size_bytes must be None for a DELETED file"):
        _change(change=FileChangeKind.DELETED, size_bytes=1)


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"path": ""}, "at least 1"),
        ({"path": "p" * (MAX_OBSERVED_PATH_CHARS + 1)}, f"at most {MAX_OBSERVED_PATH_CHARS}"),
        ({"size_bytes": -1}, "greater than or equal to 0"),
        ({"at": "2026-10-13T12:00:00"}, "timezone"),
        ({"change": "RENAMED"}, "change"),
    ],
)
def test_file_change_validators(changes: dict[str, object], reason: str) -> None:
    with pytest.raises(ValidationError, match=reason):
        _change(**changes)


# ──────────────────────────────────────────────────────────────────────────────
# Observation
# ──────────────────────────────────────────────────────────────────────────────


def test_observation_round_trips_an_empty_answer_and_a_full_one() -> None:
    empty = _observation()
    full = _observation(
        processes=(_process(),),
        resources=_resources(),
        log_lines=(LogLine(path="/var/log/syslog", line="Oct 13 12:00:00 laptop kernel: ok"),),
        file_changes=(_change(), _change(change=FileChangeKind.DELETED, size_bytes=None)),
        is_truncated=True,
    )

    for answer in (empty, full):
        assert Observation.model_validate(answer.model_dump(mode="json")) == answer
    assert empty.resources is None


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"processes": (_process(),) * (MAX_PROCESSES + 1)}, f"at most {MAX_PROCESSES}"),
        (
            {"log_lines": (LogLine(path="/x", line=""),) * (MAX_LOG_LINES + 1)},
            f"at most {MAX_LOG_LINES}",
        ),
        ({"file_changes": (_change(),) * (MAX_FILE_CHANGES + 1)}, f"at most {MAX_FILE_CHANGES}"),
        ({"observed_at": "2026-10-13T12:00:00"}, "timezone"),
        ({"is_truncated": "sometimes"}, "is_truncated"),
        ({"reason": "x" * (MAX_REASON_CHARS + 1)}, f"at most {MAX_REASON_CHARS}"),
        ({"device_id": CELL_ID}, "device_"),
        ({"screenshot": b"\x89PNG"}, "extra"),
    ],
)
def test_observation_validators(changes: dict[str, object], reason: str) -> None:
    with pytest.raises(ValidationError, match=reason):
        _observation(**changes)


# ──────────────────────────────────────────────────────────────────────────────
# The frame ceiling
# ──────────────────────────────────────────────────────────────────────────────


# Digits at their longest: a microsecond timestamp, a 17-digit float and 62-bit counters, so no
# number in the maximal answer encodes shorter than a real device's could.
_STAMP = datetime(2026, 10, 13, 12, 0, 0, 123_456, tzinfo=UTC)
_BIG = 2**62


def _maximal_resources() -> ResourceSample:
    """A resource sample with the longest numbers and every interface slot used."""
    return ResourceSample(
        cpu_load=12.345678901234567,
        memory_bytes=_BIG,
        memory_free_bytes=_BIG,
        disk_bytes=_BIG,
        disk_free_bytes=_BIG,
        uptime_s=1234567.8912345678,
        network=tuple(
            NetworkCounter(
                interface=f"{index:02d}" + "i" * (MAX_INTERFACE_CHARS - 2),
                rx_bytes=_BIG,
                tx_bytes=_BIG,
            )
            for index in range(MAX_NETWORK_INTERFACES)
        ),
    )


def _maximal_observation() -> Observation:
    """An Observation with every list full and every string at its bound, in ordinary ASCII."""
    process = ProcessSample(
        pid=4_194_304,
        name="n" * MAX_PROCESS_NAME_CHARS,
        cpu_share=0.12345678901234568,
        rss_bytes=_BIG,
        started_at=_STAMP,
    )
    line = LogLine(path="p" * MAX_OBSERVED_PATH_CHARS, line="l" * MAX_LOG_LINE_CHARS)
    change = FileChange(
        path="f" * MAX_OBSERVED_PATH_CHARS,
        change=FileChangeKind.MODIFIED,
        at=_STAMP,
        size_bytes=_BIG,
    )
    return Observation(
        device_id=DEVICE_ID,
        observed_at=_STAMP,
        processes=(process,) * MAX_PROCESSES,
        resources=_maximal_resources(),
        log_lines=(line,) * MAX_LOG_LINES,
        file_changes=(change,) * MAX_FILE_CHANGES,
        is_truncated=True,
        reason="r" * MAX_REASON_CHARS,
    )


def test_a_maximal_observation_encodes_to_well_under_the_frame_ceiling(
    make_envelope: Callable[..., Envelope],
    signed_codec: Codec,
    device_address: str,
    warden_address: str,
) -> None:
    answer = _maximal_observation()
    envelope = make_envelope(
        answer,
        correlation_id=new_message_id(CLOCK),
        sender=device_address,
        recipient=warden_address,
    )

    frame = signed_codec.encode(envelope)

    # The codec refuses a frame over the ceiling, so reaching here already proves it fits; the
    # second bound is the margin the spec promises ("well under"), so a future bump of a cap
    # cannot quietly eat it. The round trip proves the frame is also decodable whole.
    assert len(frame) < MAX_FRAME_BYTES
    assert len(frame) < 0.7 * MAX_FRAME_BYTES
    assert signed_codec.decode(frame).payload == answer


def test_a_maximal_observe_encodes_to_a_small_frame(
    make_envelope: Callable[..., Envelope],
    signed_codec: Codec,
    warden_address: str,
    device_address: str,
) -> None:
    request = _observe(
        kinds=tuple(ObservationKind),
        roots=("/" + "x" * (MAX_OBSERVED_PATH_CHARS - 1),) * MAX_OBSERVE_ROOTS,
        since_at=NOW,
        max_items=MAX_OBSERVE_ITEMS,
        reason="r" * MAX_REASON_CHARS,
    )
    envelope = make_envelope(request, sender=warden_address, recipient=device_address)

    assert len(signed_codec.encode(envelope)) < 0.7 * MAX_FRAME_BYTES
