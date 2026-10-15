"""Tests for hivemind.cell.lease: the path rule a lease judges every path by.

A lease asks one question of a path again and again: where does it really point, and is that under
scratch or under something the lease allows? Which machine answers depends on whose paths they are.
A lease on this machine asks its own filesystem (`LocalPaths`, the default and what every lease
did before). A lease on a device is given the device's rule instead, and must never ask the Hive
Stand's disk about another machine's paths: it would follow the wrong symbolic links and resolve a
relative path against the wrong working directory.

Fits into the Hive:
    Mirrors the path rule in src/hivemind/cell/lease.py (codingrules section 3, split by feature
    per 14.2 from test_lease.py). The filesystem is a real temporary directory with a real symbolic
    link in it; the "device" rule is a lexical stand-in, and the real one is tested beside the
    Swarm's own paths (tests/unit/swarm/session/test_paths.py).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.cell.lease for the module under test.
    - hivemind.swarm.session.paths for the device rule a Swarm lease is given.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from builders.cells import make_identity, make_lease_request

from hivemind.cell.fake import FakeLeaseReleaser
from hivemind.cell.lease import LOCAL_PATHS, LeaseFacts, LocalPaths, PathRule, RealCellLease
from hivemind.cell.tiers import CombShieldLevel
from hivemind.pheromone import TrailQuery
from hivemind.pheromone.trail.memory import MemoryPheromoneTrail
from waggle.clock import FakeClock
from waggle.ids import new_lease_id


class _LexicalRule:
    """A rule that reads paths as text and never touches a disk: a device's, in miniature."""

    def __init__(self) -> None:
        self.asked: list[Path] = []

    def resolve(self, path: Path) -> Path:
        self.asked.append(path)
        # Lexical in this host's own spelling: the scratch is a temporary directory spelled as
        # this host spells it, and `posixpath` would leave a Windows path's `..` in place.
        return Path(os.path.normpath(path))

    def is_within(self, path: Path, root: Path) -> bool:
        return path == root or root in path.parents


def _lease(
    scratch: Path, rule: PathRule | None = None, allowed: tuple[Path, ...] = ()
) -> tuple[RealCellLease, MemoryPheromoneTrail]:
    """A lease over `scratch`, judging paths by `rule` (the local rule when omitted)."""
    clock = FakeClock()
    request = make_lease_request(clock=clock, allowed_paths=allowed)
    trail = MemoryPheromoneTrail(clock)
    fields: dict[str, object] = {
        "id": new_lease_id(clock),
        "cell_id": request.cell_id,
        "holder": request.holder,
        "task_id": request.task_id,
        "scratch_root": scratch,
        "access_level": request.access_level,
        "comb_shield": CombShieldLevel.MEADOW,
        "allowed_paths": allowed,
    }
    if rule is not None:
        fields["paths"] = rule
    lease = RealCellLease(
        LeaseFacts(**fields),  # type: ignore[arg-type]  # Every field is named above.
        trail=trail,
        clock=clock,
        identity=make_identity(clock=clock),
        releaser=FakeLeaseReleaser(),
    )
    return lease, trail


@pytest.fixture
def scratch(tmp_path: Path) -> Path:
    """A scratch directory with a symbolic link inside it that leads out of it."""
    root = tmp_path / "scratch"
    root.mkdir()
    (tmp_path / "outside").mkdir()
    (root / "link").symlink_to(tmp_path / "outside")
    return root


def test_the_local_rule_resolves_against_the_real_filesystem(scratch: Path, tmp_path: Path) -> None:
    resolved = LocalPaths().resolve(scratch / "a" / ".." / "link" / "f")

    assert resolved == (tmp_path / "outside" / "f").resolve()  # `..` collapsed, link followed.


def test_the_local_rule_is_within_means_equal_or_under_and_not_a_shared_prefix() -> None:
    rule = LocalPaths()
    root = Path("/work/scratch")

    assert rule.is_within(Path("/work/scratch"), root)
    assert rule.is_within(Path("/work/scratch/a/b"), root)
    assert not rule.is_within(Path("/work/scratch2/a"), root)  # Not a path under it.
    assert not rule.is_within(Path("/work"), root)


def test_a_lease_that_is_given_no_rule_uses_the_local_one(scratch: Path) -> None:
    lease, _trail = _lease(scratch)

    assert not lease.is_path_allowed(scratch / "link" / "f")  # The link leads out of scratch.
    assert lease.is_path_allowed(scratch / "a" / ".." / "b")
    assert LOCAL_PATHS.is_within(Path("/a/b"), Path("/a"))


def test_a_lease_given_a_device_rule_never_asks_the_filesystem(scratch: Path) -> None:
    rule = _LexicalRule()
    lease, _trail = _lease(scratch, rule)

    # The same path the local rule refuses: on a device the link is the device's to judge.
    assert lease.is_path_allowed(scratch / "link" / "f")
    assert rule.asked  # And it was the rule that was asked.


def test_a_path_outside_scratch_is_not_allowed_by_any_rule(scratch: Path, tmp_path: Path) -> None:
    for rule in (None, _LexicalRule()):
        lease, _trail = _lease(scratch, rule)

        assert not lease.is_path_allowed(tmp_path / "outside" / "f")
        assert not lease.is_path_allowed(scratch / ".." / "outside")


def test_allowing_a_path_stores_what_the_rule_resolved_it_to(scratch: Path) -> None:
    lease, _trail = _lease(scratch, _LexicalRule())

    lease.note_allowed_path(Path("/home/dev/project/../project"))

    assert lease.allowed_paths == (Path("/home/dev/project"),)
    assert lease.is_path_allowed(Path("/home/dev/project/src/main.py"))
    lease.note_allowed_path(Path("/home/dev/project/src"))  # Already reachable: not stored twice.
    assert lease.allowed_paths == (Path("/home/dev/project"),)


async def test_a_path_touched_outside_scratch_is_recorded_as_the_rule_resolved_it(
    scratch: Path,
) -> None:
    lease, trail = _lease(scratch, _LexicalRule())
    await lease.open()

    await lease.note_touched_path(Path("/etc/./hosts"))
    await lease.note_touched_path(scratch / "link" / "inside.txt")  # Inside, to the device.

    assert lease.touched_paths == (Path("/etc/hosts"), scratch / "link" / "inside.txt")
    [event] = await trail.query(TrailQuery(kind="cell.touched_outside_scratch"))
    # Only the one outside scratch is recorded, as the text of the path the rule resolved it to.
    assert event.payload["path"] == str(Path("/etc/hosts"))


def test_a_restore_record_for_a_path_outside_scratch_carries_the_resolved_path(
    scratch: Path,
) -> None:
    lease, _trail = _lease(scratch, _LexicalRule())

    lease.note_restore_path(Path("/etc/./hosts"), b"prior")
    lease.note_restore_path(scratch / "link" / "inside.txt", None)  # Inside: nothing to restore.

    [record] = lease.restore_records
    assert record.path == Path("/etc/hosts") and record.prior == b"prior"
