"""Phase 11 step 11.10 on a second machine: an idle device's Warden reads the directories named.

"Observes within the Cell's access level (process list, resource use, logs and file-change events
in allowed roots, network counters)." The device is a container running the Pollen Packet; its
Warden runs on the Hive Stand and asks the packet (`swarm.observe`), which answers from fixed
collectors with no lease and no command. The operator names the device's directories with `hive
swarm watch` (run through Typer's runner against the Hive's own database, as a person would), the
running Hive rebuilds the device's Warden with them, and the Warden's probe asks for exactly them.
Sampling is compressed to a fifth of a second and the Patrol is put an hour away, so that every
observation the Warden keeps is still there to be read when the scenario ends.

What it proves, in three steps on one device. With `/srv/watched` named: its log lines and a file
changed in it reach an observation, a new log line and a changed file included, while a log and a
file that changed in `/srv/elsewhere` (and one written through a symbolic link inside the watched
directory that leads there) never do. With `/srv/elsewhere` named too: the files outside were there
to be read all along, so what kept them out was the list. With `--clear`: the Warden's next
observations hold no log line and no file change at all. And the whole time the Hive sent the
device no command and wrote nothing on it outside the packet's own directory.

Fits into the Hive:
    Test infrastructure (codingrules section 14.2), not shipped. Needs a docker daemon and skips
    cleanly without one.

Key invariants:
    - The files under `/srv` are the test's own: the device holds nothing there that the Hive wrote.
    - Every container and network is removed, whatever the test did: the janitor closes in a
      `finally`.
    - Waits are bounded polls of what the Warden has kept, never bare sleeps.

See Also:
    - .claude/roadmap.md phase 11 "Exit criteria", the overnight bullet; step 11.10.
    - tests/e2e/test_phase11_exit_watch.py for the same device watched for processes and CPU.
    - docs/adr/0054-access-levels-watch-mode-and-the-patrol.md for where the directories come from.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime
from functools import partial
from pathlib import Path

import pytest
from e2e.kernel_helpers import wait_until
from e2e.swarm.bridge import BridgeHive, BridgeTerms, bridge_hive
from e2e.swarm.container import Container, Janitor, Network
from e2e.swarm.enrolled import DATA_DIR, DeviceOptions, enrol_device
from e2e.swarm.hive import trail_events, warden_is_idle
from e2e.swarm.image import device_image_or_skip
from e2e.swarm.operator import swarm
from e2e.swarm.snapshots import changes_outside
from e2e.watch_helpers import PatrolModel, kept_by

from hivemind.cli.compose import Hive, run_hive
from hivemind.swarm import SwarmNode

pytestmark = pytest.mark.e2e

_WAIT_S = 90.0  # Each wait; a container enrols in seconds and the Hive rebuilds a Warden in two.
_WATCHED = "/srv/watched"  # The directory the operator names first.
_ELSEWHERE = "/srv/elsewhere"  # One on the same device that nobody asked the Warden to read.
_LOG = f"{_WATCHED}/app.log"
_DATA = f"{_WATCHED}/sub/data.txt"
_OUTSIDE_LOG = f"{_ELSEWHERE}/secret.log"
_LINK = f"{_WATCHED}/escape"  # A symbolic link inside the watched directory, leading outside it.
# Sampling every fifth of a second, and a Patrol an hour away: a quiet Patrol discards what it
# covered, and this scenario needs everything that was kept.
_WATCH_TABLE = (
    "interval_s = 0.2\nretention_s = 86400.0\npatrol_interval_s = 3600.0\n"
    "cpu_share_alarm = 1.0\ndisk_fill_alarm = 1.0\nroots = []\n"
)
_PLANT = (
    f"mkdir -p {_WATCHED}/sub {_ELSEWHERE} && echo 'watched service started' > {_LOG} && "
    f"echo 'v1' > {_DATA} && echo 'outside service started' > {_OUTSIDE_LOG} && "
    f"echo 'v1' > {_ELSEWHERE}/other.txt && ln -s {_ELSEWHERE} {_LINK}"
)
# What happens on the device while it is watched: a log line and a changed file in the watched
# directory, the same two outside it, and a file written through the link.
_HAPPEN = (
    f"echo 'watched service lost its database' >> {_LOG} && echo 'version two' > {_DATA} && "
    f"echo 'outside service lost its database' >> {_OUTSIDE_LOG} && "
    f"echo 'version two' > {_ELSEWHERE}/other.txt && "
    f"echo 'through the link' >> {_LINK}/via-link.log"
)
_NEW_LINE = (_LOG, "watched service lost its database")
_FIRST_LINE = (_LOG, "watched service started")
_OUTSIDE_LINES = {
    (_OUTSIDE_LOG, "outside service started"),
    (_OUTSIDE_LOG, "outside service lost its database"),
}
_QUIET = 3  # How many consecutive observations without a file read show the reading has stopped.


@dataclass(frozen=True, slots=True)
class _Evidence:
    """Everything the device's Warden kept in a stretch of time, boiled down to what it read."""

    count: int  # How many observations.
    lines: frozenset[tuple[str, str]]  # Each (log path, line) any observation held.
    changes: frozenset[tuple[str, str]]  # Each (kind, path) of a file change any held.

    @property
    def paths(self) -> set[str]:
        """Every path a log line or a file change named."""
        return {path for path, _ in self.lines} | {path for _, path in self.changes}


@pytest.fixture(scope="module")
def image() -> str:
    """The device image for this tree; the module skips without a docker daemon."""
    return device_image_or_skip()


def test_an_idle_devices_warden_reads_the_directories_the_operator_named_and_only_those(
    image: str, tmp_path: Path
) -> None:
    janitor = Janitor()
    try:
        network = janitor.network()
        terms = BridgeTerms(watch_table=_WATCH_TABLE)
        hive = bridge_hive(tmp_path, network, PatrolModel().responder, terms)

        asyncio.run(_scenario(hive, janitor, image, (network, tmp_path / "hive.toml")))
    finally:
        janitor.close()


async def _scenario(hive: Hive, janitor: Janitor, image: str, where: tuple[Network, Path]) -> None:
    """Enrol a container, name its directories three ways, and read what its Warden kept."""
    network, manifest = where
    async with run_hive(hive):
        device = await enrol_device(
            BridgeHive(hive), janitor, image, DeviceOptions(network=network)
        )
        container, node = device.container, device.node
        await wait_until(lambda: warden_is_idle(hive, node), timeout_s=_WAIT_S)
        await asyncio.to_thread(container.exec, ["sh", "-c", _PLANT])

        # With no directory named it reads processes and resource use only.
        await _wait_for_looks(hive, node, 3)
        assert (await _evidence(hive, node)).paths == set()

        await _name(manifest, node, _WATCHED)
        await wait_until(lambda: _read(hive, node, _FIRST_LINE), timeout_s=_WAIT_S)
        await asyncio.to_thread(container.exec, ["sh", "-c", _HAPPEN])
        await wait_until(lambda: _read(hive, node, _NEW_LINE), timeout_s=_WAIT_S)
        await wait_until(lambda: _changed(hive, node, _DATA), timeout_s=_WAIT_S)
        before_naming_more = hive.clock.now()
        await _only_the_watched_directory_was_read(hive, node, before_naming_more)

        await _name(manifest, node, _WATCHED, _ELSEWHERE)
        for outside in sorted(_OUTSIDE_LINES):
            await wait_until(partial(_read, hive, node, outside), timeout_s=_WAIT_S)

        await _name(manifest, node)  # No directory: `--clear`.
        await _the_reading_stops(hive, node)
        await _the_device_was_only_watched(hive, node, container)


async def _name(manifest: Path, node: SwarmNode, *directories: str) -> None:
    """Run `hive swarm watch` for the device, as the operator types it; none means `--clear`."""
    named = list(directories) or ["--clear"]
    result = await swarm(manifest, "watch", str(node.label), *named)
    assert result.exit_code == 0, result.stderr


async def _evidence(hive: Hive, node: SwarmNode, until: datetime | None = None) -> _Evidence:
    """Boil the observations the Warden kept (up to `until`) down to what they read."""
    everything = await kept_by(hive, node.warden_id)
    kept = [seen for seen in everything if until is None or seen.kept_at <= until]
    lines = {(line.path, line.line) for seen in kept for line in seen.observation.log_lines}
    changes = {
        (change.change.value, change.path)
        for seen in kept
        for change in seen.observation.file_changes
    }
    return _Evidence(len(kept), frozenset(lines), frozenset(changes))


async def _read(hive: Hive, node: SwarmNode, line: tuple[str, str]) -> bool:
    """Whether some observation holds this log line of this file."""
    return line in (await _evidence(hive, node)).lines


async def _changed(hive: Hive, node: SwarmNode, path: str) -> bool:
    """Whether some observation records a change to this file."""
    return any(changed == path for _, changed in (await _evidence(hive, node)).changes)


async def _wait_for_looks(hive: Hive, node: SwarmNode, count: int) -> None:
    """Wait until the Warden has kept `count` observations."""

    async def enough() -> bool:
        return (await _evidence(hive, node)).count >= count

    await wait_until(enough, timeout_s=_WAIT_S)


async def _only_the_watched_directory_was_read(
    hive: Hive, node: SwarmNode, until: datetime
) -> None:
    """Nothing the Warden kept up to `until` names a path outside the directory it was told."""
    seen = await _evidence(hive, node, until)
    assert seen.lines >= {_FIRST_LINE, _NEW_LINE}
    assert seen.paths, "the Warden kept no log line or file change at all"
    # Every path is under the watched directory; the file written through the link is not there,
    # because the packet's walk never follows a symbolic link out of a directory.
    assert all(path.startswith(f"{_WATCHED}/") for path in seen.paths), sorted(seen.paths)
    assert not any(path.startswith(f"{_LINK}/") or path == _LINK for path in seen.paths)
    assert not seen.lines & _OUTSIDE_LINES


async def _the_reading_stops(hive: Hive, node: SwarmNode) -> None:
    """With no directory named, the Warden's newest observations hold no log line or change."""
    cleared_at = hive.clock.now()

    async def quiet() -> bool:
        kept = [seen for seen in await kept_by(hive, node.warden_id) if seen.kept_at > cleared_at]
        newest = kept[-_QUIET:]
        return len(newest) == _QUIET and not any(
            seen.observation.log_lines or seen.observation.file_changes for seen in newest
        )

    await wait_until(quiet, timeout_s=_WAIT_S)


async def _the_device_was_only_watched(hive: Hive, node: SwarmNode, container: Container) -> None:
    """The Hive sent the device no command and wrote nothing on it but in the test's own tree."""
    assert await trail_events(hive, "swarm.command_sent") == []
    assert warden_is_idle(hive, node)
    # `/srv` is where the test planted its files; the packet's own directory is its own. Nothing
    # else on the device was written: not a file the Hive made, not a scratch directory.
    assert await asyncio.to_thread(changes_outside, container, DATA_DIR, "/srv") == []
    tree = await asyncio.to_thread(container.exec, ["find", "/srv", "-type", "f"])
    assert sorted(tree.stdout.split()) == sorted(
        [_LOG, _DATA, _OUTSIDE_LOG, f"{_ELSEWHERE}/other.txt", f"{_ELSEWHERE}/via-link.log"]
    )
