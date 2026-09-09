"""Unit tests for hivemind.cell.local.quota, including the roadmap step 3.11 nested-archive test.

Fits into the Hive:
    Mirrors src/hivemind/cell/local/quota.py (codingrules section 3: tests/unit mirrors src/
    one-to-one). `test_a_nested_archive_extraction_over_quota_is_stopped_and_cleaned_up` is the
    roadmap step's own "extracts a nested archive and asserts the lease stops it under the cap."

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.cell.local.quota for directory_size_bytes, DirectorySizer and ScratchQuota, under
      test directly.
    - hivemind.cell.local.session for LocalProcessSession, the watchdog under test indirectly.
"""

from __future__ import annotations

import io
import sys
import zipfile
from pathlib import Path

import pytest
from builders.cells import make_real_cell_lease

from hivemind.cell.errors import ScratchQuotaExceededError
from hivemind.cell.lease import RealCellLease
from hivemind.cell.local.quota import DirectorySizer, ScratchQuota, directory_size_bytes
from hivemind.cell.local.releaser import HiveStandLeaseReleaser
from hivemind.cell.local.session import LocalProcessSession
from hivemind.cell.session import ExecSpec, run
from waggle.clock import SystemClock

_PAYLOAD_BYTES = 16 * 1024 * 1024  # ~16 MiB of zeros, matching the roadmap step's own figure.
_QUOTA_BYTES = 4 * 1024 * 1024  # 4 MB, matching the roadmap step's own figure.
_EXTRACT_TIMEOUT_S = 10.0  # Generous; the watchdog is expected to end this long before it matters.
# `python -m zipfile -e` writes an all-zeros member fast enough that a bare extraction usually
# finishes before the watchdog gets a chance to interrupt it, so a second extraction below writes
# more slowly on purpose, giving the watchdog several real ticks to run against a real, still-busy
# child before the test's own scripted DirectorySizer (below) forces the breach.
_THROTTLE_CHUNK_BYTES = 262_144  # 256 KiB per write.
_THROTTLE_SLEEP_S = 0.05  # Comfortably shorter than QUOTA_SAMPLE_INTERVAL_S's own 0.25 s tick.
_THROTTLED_EXTRACT_SCRIPT = (
    "import time, zipfile\n"
    "with zipfile.ZipFile('inner.zip') as zf, zf.open('payload.bin') as src, "
    "open('payload.bin', 'wb') as dst:\n"
    f"    chunk = src.read({_THROTTLE_CHUNK_BYTES})\n"
    "    while chunk:\n"
    "        dst.write(chunk)\n"
    f"        time.sleep({_THROTTLE_SLEEP_S})\n"
    f"        chunk = src.read({_THROTTLE_CHUNK_BYTES})\n"
)
_BREACH_AFTER_TICKS = 2  # How many real watchdog ticks _ScriptedSizer lets pass before forcing it.
_FORCED_BREACH_BYTES = _QUOTA_BYTES + 1  # Just over the cap; the exact value the test asserts on.


def test_directory_size_bytes_sums_nested_files(tmp_path: Path) -> None:
    (tmp_path / "a.bin").write_bytes(b"x" * 10)
    nested = tmp_path / "nested"
    nested.mkdir()
    (nested / "b.bin").write_bytes(b"y" * 20)

    assert directory_size_bytes(tmp_path) == 30


def test_directory_size_bytes_is_zero_for_an_empty_directory(tmp_path: Path) -> None:
    assert directory_size_bytes(tmp_path) == 0


def test_directory_size_bytes_is_zero_for_a_directory_that_does_not_exist(tmp_path: Path) -> None:
    assert directory_size_bytes(tmp_path / "gone") == 0


def test_directory_size_bytes_never_follows_a_symlinked_file(tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside-quota-symlink-target.bin"
    outside.write_bytes(b"z" * 1_000)
    link = tmp_path / "linked.bin"
    try:
        link.symlink_to(outside)
    except OSError:
        # Creating a symlink needs a privilege this account may not hold (notably on Windows
        # without Developer Mode); the behaviour this test checks does not depend on the host.
        pytest.skip("this host will not create a symlink for this test")

    assert directory_size_bytes(tmp_path) == 0
    outside.unlink()


def test_scratch_quota_holds_the_configured_byte_cap() -> None:
    assert ScratchQuota(quota_bytes=1024).quota_bytes == 1024


async def _open_lease_and_extract_outer_zip(tmp_path: Path) -> tuple[RealCellLease, SystemClock]:
    """Open a real lease and run the tiny, near-instant first extraction (outer.zip -> inner.zip).

    Split out of the roadmap step 3.11 nested-archive test so that test's own body stays under
    codingrules 5.1's 50-line function limit; this half sets the stage, the test's own body drives
    and asserts on the one extraction that actually breaches the quota.

    Args:
        tmp_path: The test's own temporary directory, holding the lease's scratch root.

    Returns:
        The opened lease (with a real HiveStandLeaseReleaser, not make_real_cell_lease's own
        default FakeLeaseReleaser, since this test needs release() to really kill and really
        remove) and the real clock it was built with.
    """
    scratch_root = tmp_path / "scratch"
    scratch_root.mkdir()
    clock = SystemClock()  # Real subprocess timing needs real time, not a FakeClock.
    lease = make_real_cell_lease(scratch_root, clock=clock, releaser=HiveStandLeaseReleaser(clock))
    await lease.open()
    # A plain, unscripted quota: outer.zip -> inner.zip is tiny and near-instant, so it never
    # comes close to the cap and needs nothing special.
    setup_session = LocalProcessSession(lease, ScratchQuota(quota_bytes=_QUOTA_BYTES), clock)
    await setup_session.put_file(Path("outer.zip"), _build_nested_zip(bytes(_PAYLOAD_BYTES)))
    await run(
        setup_session,
        ExecSpec(
            argv=(sys.executable, "-m", "zipfile", "-e", "outer.zip", "."),
            timeout_s=_EXTRACT_TIMEOUT_S,
        ),
    )
    return lease, clock


async def test_a_nested_archive_extraction_over_quota_is_stopped_and_cleaned_up(
    tmp_path: Path,
) -> None:
    lease, clock = await _open_lease_and_extract_outer_zip(tmp_path)
    scratch_root = lease.scratch_root

    # Second extraction: inner.zip -> payload.bin, the real (uncompressed) payload, written out
    # slowly on purpose (see _THROTTLED_EXTRACT_SCRIPT) so a real, still-busy child is what the
    # watchdog is polling against. This is the one the watchdog must stop -- a fresh session over
    # the same lease, with a scripted sizer (see _ScriptedSizer's own docstring for why the
    # crossing itself is test-controlled rather than read from the real filesystem): real
    # cross-process file-size polling is not reliably live everywhere while a write is in flight,
    # so asserting on exactly how many real bytes leaked through before a real poll noticed would
    # make this test racy against the host's own I/O and scheduling behaviour, not against this
    # package's own logic.
    sizer = _ScriptedSizer(real=directory_size_bytes, breach_after=_BREACH_AFTER_TICKS)
    quota = ScratchQuota(quota_bytes=_QUOTA_BYTES, sizer=sizer)
    session = LocalProcessSession(lease, quota, clock)
    with pytest.raises(ScratchQuotaExceededError) as caught:
        await run(
            session,
            ExecSpec(
                argv=(sys.executable, "-c", _THROTTLED_EXTRACT_SCRIPT),
                timeout_s=_EXTRACT_TIMEOUT_S,
            ),
        )

    assert caught.value.quota_bytes == _QUOTA_BYTES
    # Deterministic: the raised error's own observed_bytes is exactly what the scripted sizer
    # reported on the call that tripped the breach, not a wall-clock-derived guess about how much
    # of the real payload had landed on disk by then.
    assert caught.value.observed_bytes == _FORCED_BREACH_BYTES
    # The watchdog genuinely polled more than once against the real, still-running child before
    # the scripted breach fired -- proving this is a real mid-command stop, not merely a
    # post-completion check against a child that had already exited on its own.
    assert sizer.calls > _BREACH_AFTER_TICKS

    await lease.release()
    assert not scratch_root.exists()


class _ScriptedSizer:
    """A DirectorySizer that lets a few real ticks pass, then forces the quota breach on demand.

    Real cross-process file-size polling is not reliably live everywhere while another process
    still holds the file open for writing (codingrules section 14: a test must not depend on
    scheduling or filesystem-caching behaviour outside this package's own control), so this sizer
    does not decide the breach from what `real` reports. It still calls `real` every tick (so the
    watchdog is genuinely polling a real, growing scratch directory throughout) and counts those
    calls in `self.calls`, but forces `_FORCED_BREACH_BYTES` -- a value over `_QUOTA_BYTES` known
    to the test -- once `breach_after` real ticks have already happened, giving the test a fixed,
    host-independent point at which the watchdog must stop the child.
    """

    def __init__(self, real: DirectorySizer, breach_after: int) -> None:
        """Build a sizer that reports `real`'s own readings for `breach_after` ticks, then breaches.

        Args:
            real: The real sizer to poll (and count calls to) every tick.
            breach_after: How many calls to make before forcing the breach on every call after.
        """
        self._real = real
        self._breach_after = breach_after
        self.calls = 0

    def __call__(self, root: Path) -> int:
        """Report `real(root)` for the first `breach_after` calls, then `_FORCED_BREACH_BYTES`."""
        self._real(root)  # Still read the real directory, so the watchdog polls real growth.
        self.calls += 1
        if self.calls <= self._breach_after:
            return 0  # Comfortably under quota: the watchdog must not stop the child yet.
        return _FORCED_BREACH_BYTES


def _build_nested_zip(payload: bytes) -> bytes:
    """Build an outer zip containing an inner zip containing one file: `payload`.

    Mirrors the roadmap step's own fixture: "a small zip containing another zip whose member is
    ...zeros," so the first extraction stays trivially small and only the second breaches quota.
    """
    inner_buffer = io.BytesIO()
    with zipfile.ZipFile(inner_buffer, "w", zipfile.ZIP_DEFLATED) as inner_zip:
        inner_zip.writestr("payload.bin", payload)
    outer_buffer = io.BytesIO()
    with zipfile.ZipFile(outer_buffer, "w", zipfile.ZIP_DEFLATED) as outer_zip:
        outer_zip.writestr("inner.zip", inner_buffer.getvalue())
    return outer_buffer.getvalue()
