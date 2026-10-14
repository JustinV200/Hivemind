"""Make a container device colonizable, and put a scripted model on each side of its cable.

A Level 0 device only has a packet on it. To be moved up the ladder it needs the three things an
operator puts there at `pollen install`: the wheelhouse the Hive runtime installs from (the device
image carries one, built from this tree), the device's own manifest, and, for a Nuc, the model
server that manifest declares. `colonizable` writes the manifest and the server's rounds into the
scenario's own directory and returns the `DeviceOptions` that copy them into the container before
its packet starts and run `pollen install` between enrolling and running, so the container is
started the way a person would set the device up, with nothing the Hive Stand could reach in.

The Hive Stand's side of the cable is a scripted OpenAI-compatible server on the network's host
address (`shared_model`): the model a colonized Warden's sub-bees think on until the device has one
of its own, and the one a task that needs a stronger model than the device's reaches for. The
device's side is the same server, started inside the container by the Warden itself when the Queen
promotes the device, from the copy of its source that `colonizable` put there.

Fits into the Hive:
    Test infrastructure (codingrules section 14.3), not shipped. Used by the phase 11 Nuc scenario;
    builds on `e2e.swarm.enrolled`, `e2e.swarm.container` and `e2e.scripted_openai`.

Key invariants:
    - Everything the container reads at start is copied in before it starts, so the first command
      that reads a file finds it.
    - The shared server binds the network's host address only, never every interface, and stops
      with the block that started it.
    - The device's manifest names the shared server by an address the container reaches, never a
      loopback one, which would be the container's own.

See Also:
    - e2e/swarm/colony/models.py for the text of the manifests and the scenarios.
    - e2e/swarm/device/Dockerfile for the wheelhouse the container installs the runtime from.
"""

from __future__ import annotations

import json
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

from e2e.scripted_openai import Scenario, Step, serve_in_thread
from e2e.swarm.colony.models import (
    LOCAL_SCENARIO,
    RUNTIME_PYTHON,
    SCRIPTED_SERVER,
    WARDEN_MANIFEST,
    device_manifest,
    scenario_text,
)
from e2e.swarm.container import Container, Network
from e2e.swarm.enrolled import DATA_DIR, DeviceOptions

WHEELHOUSE = "/opt/wheelhouse"  # Where the device image keeps the wheels the runtime installs from.
WARDEN_LOG = f"{DATA_DIR}/runtime/warden.log"  # What the on-device Warden writes.
PREPARE_LOG = f"{DATA_DIR}/runtime/prepare.log"  # What the runtime's installation wrote.
STATE_DIR = f"{DATA_DIR}/runtime/state"  # Where the on-device Warden keeps its trail and outbox.
# Read by the runtime's own interpreter, the only Python on the device, with the path of the file
# as its first argument. The outbox is a log of `put` and `ack` lines (waggle.outbox.log), so what
# is still queued is what was put and not acked.
_OUTBOX_READER = (
    "import json, sys\n"
    "queued = {}\n"
    "with open(sys.argv[1]) as log:\n"
    "    for line in log:\n"
    "        row = json.loads(line)\n"
    "        if row['op'] == 'put':\n"
    "            queued[row['id']] = row['frame']\n"
    "        else:\n"
    "            queued.pop(row['id'], None)\n"
    "frames = [json.loads(frame) for frame in queued.values()]\n"
    "print(json.dumps([[f['kind'], f['payload'].get('task_id')] for f in frames]))\n"
)
# The trail is read-only here, and the kind is a bound parameter, not text in the query.
_EVENTS_READER = (
    "import json, sqlite3, sys\n"
    "db = sqlite3.connect('file:' + sys.argv[1] + '?mode=ro', uri=True)\n"
    "query = 'select body from pheromone_events where kind = ? order by at'\n"
    "bodies = [json.loads(row[0]) for row in db.execute(query, (sys.argv[2],))]\n"
    "db.close()\n"
    "print(json.dumps(bodies))\n"
)

_IDS_READER = (
    "import json, sqlite3, sys\n"
    "db = sqlite3.connect('file:' + sys.argv[1] + '?mode=ro', uri=True)\n"
    "rows = db.execute('select id, at from pheromone_events').fetchall()\n"
    "db.close()\n"
    "print(json.dumps(rows))\n"
)

__all__ = [
    "PREPARE_LOG",
    "STATE_DIR",
    "WARDEN_LOG",
    "WHEELHOUSE",
    "colonizable",
    "device_event_ids",
    "device_events",
    "device_logs",
    "device_outbox",
    "scenario_of",
    "shared_model",
]


def scenario_of(fields: Mapping[str, object]) -> Scenario:
    """Build the scripted server's `Scenario` from the fields of a scenario file.

    Args:
        fields: What `e2e.swarm.colony.models.shared_scenario` returns: `steps` as JSON objects
            and a `closing` line.

    Returns:
        The scenario the in-process server runs, its steps made `Step`s.
    """
    steps = fields["steps"]
    assert isinstance(steps, list)
    return Scenario(
        steps=tuple(Step(**step) for step in steps),
        closing=str(fields["closing"]),
    )


@contextmanager
def shared_model(network: Network, scenario: Scenario) -> Iterator[str]:
    """Serve `scenario` on the network's host address; yield the base URL a container reaches it by.

    Args:
        network: The container network; its host address is where the server binds.
        scenario: What the server answers.

    Yields:
        The `/v1` URL of the server as the container sees it.
    """
    with serve_in_thread(scenario, host=network.gateway) as loopback:
        port = urlsplit(loopback).port
        yield f"http://{network.gateway}:{port}/v1"


def colonizable(
    directory: Path,
    network: Network,
    shared_url: str,
    local: Mapping[str, object],
    supervision: str = "",
) -> DeviceOptions:
    """Write a colonizable device's files and return the options that enrol it with them.

    Args:
        directory: The scenario's own directory; the files are written to it before they are
            copied into the container.
        network: The container network the device is started on.
        shared_url: The shared model server's `/v1` URL as the container reaches it.
        local: The rounds the device's own model server makes (`models.local_scenario`).
        supervision: A `[supervision]` table for the device's manifest (see `device_manifest`).

    Returns:
        Options that copy the manifest, the server's source and its rounds into the container, and
        install the packet with the wheelhouse and the manifest, for a device that can be colonized
        and promoted.
    """
    manifest = directory / "warden.toml"
    manifest.write_text(device_manifest(shared_url, supervision=supervision), encoding="utf-8")
    rounds = directory / "local-scenario.json"
    rounds.write_text(scenario_text(dict(local)), encoding="utf-8")
    source = Path(__file__).resolve().parents[2] / "scripted_openai.py"
    files = {WARDEN_MANIFEST: manifest, LOCAL_SCENARIO: rounds, SCRIPTED_SERVER: source}
    install = ("--runtime-wheelhouse", WHEELHOUSE, "--warden-manifest", WARDEN_MANIFEST)
    return DeviceOptions(network=network, files=files, install=install)


def device_logs(container: Container) -> str:
    """The packet's output and the Warden's and installer's logs, for a failure to be read by."""
    logs = [container.logs()]
    for path in (PREPARE_LOG, WARDEN_LOG):
        shown = container.exec(["sh", "-c", f"tail -n 60 {path} 2>&1"], check=False)
        logs.append(f"--- {path}\n{shown.stdout}")
    return "\n".join(logs)


def device_outbox(container: Container) -> list[tuple[str, str | None]]:
    """What the on-device Warden has queued for the Queen and she has not yet had.

    Args:
        container: The device.

    Returns:
        The kind of each frame still in the outbox and the task it names, if any, oldest first.
        Empty when the Warden has not queued anything (or has not yet created the file).
    """
    path = f"{STATE_DIR}/outbox.jsonl"
    shown = container.exec([RUNTIME_PYTHON, "-c", _OUTBOX_READER, path], check=False)
    if shown.exit_code != 0:
        return []  # No outbox file yet: nothing was ever queued.
    return [(kind, task) for kind, task in json.loads(shown.stdout)]


def device_events(container: Container, kind: str) -> list[dict[str, object]]:
    """The events of `kind` in the on-device Warden's own trail, oldest first, as recorded.

    Args:
        container: The device.
        kind: An event kind, such as `warden.offline`.

    Returns:
        Each event's whole body (id, node, time, kind, subject and payload); empty when the Warden
        has recorded none, or has not yet created its trail.
    """
    path = f"{STATE_DIR}/warden.sqlite"
    shown = container.exec([RUNTIME_PYTHON, "-c", _EVENTS_READER, path, kind], check=False)
    if shown.exit_code != 0:
        return []  # No trail yet: the Warden has not started.
    events: list[dict[str, object]] = json.loads(shown.stdout)
    return events


def device_event_ids(container: Container) -> dict[str, datetime]:
    """Every event the on-device Warden has recorded, by id, with the moment it recorded it.

    Args:
        container: The device.

    Returns:
        Event id to time; empty when the Warden has not created its trail.
    """
    path = f"{STATE_DIR}/warden.sqlite"
    shown = container.exec([RUNTIME_PYTHON, "-c", _IDS_READER, path], check=False)
    if shown.exit_code != 0:
        return {}
    rows: list[list[str]] = json.loads(shown.stdout)
    return {event_id: _utc(at) for event_id, at in rows}


def _utc(text: str) -> datetime:
    """Read a stored time, which ends in `Z` or an offset, as an aware UTC datetime."""
    return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(UTC)
