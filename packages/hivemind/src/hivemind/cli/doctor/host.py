"""Check what `hive doctor` needs of this machine: the scratch root, sqlite-vec and Docker.

Three checks that read the host rather than the models. The scratch root is where every Hive Stand
lease gets its working directory: it is created on demand, so the check is that it (or its
nearest existing parent) is a writable directory with more free disk than `[hive_stand]
disk_reserve_mb`. sqlite-vec is loaded onto a throwaway in-memory database exactly as the Honey
Store loads it; when it cannot load, the Honey Store falls back to Python distances (ADR-0035), so
that is a warning, not a failure. Docker is probed only when `[virtual_cells] backend = "docker"`,
through an injected probe so tests never need a daemon.

Fits into the Hive:
    Layer 7 (the terminal), inside `hivemind.cli.doctor`. Called by
    `hivemind.cli.doctor.command`. Calls into `hivemind.honey_store` (the vector loader),
    `hivemind.common.sqlite`, `hivemind.hive.backends.docker.sdk_client` (the real probe) and
    `hivemind.manifest`.

Key invariants:
    - Nothing is created or left behind: the scratch root is probed with a temporary file only
      when it already exists, and the vector check uses an in-memory database.
    - Every failure becomes a `Check`; nothing here raises.

See Also:
    - hivemind.honey_store.store.sqlite.vec for the loader this reuses.
"""

from __future__ import annotations

import shutil
import tempfile
from collections.abc import Awaitable, Callable
from pathlib import Path

from hivemind.cli.doctor.report import Check, Outcome
from hivemind.common.sqlite import connect
from hivemind.hive.backends.docker.sdk_client import SdkDockerClient
from hivemind.honey_store import load_vector_extension
from hivemind.manifest import HiveManifest

BYTES_PER_MB = 1024 * 1024  # `disk_reserve_mb` is in mebibytes, as the Hive Stand reads it.
_PROBE_LABELS = {"hivemind.doctor": "probe"}  # Matches no container: the list call is the probe.

DockerProbe = Callable[[], Awaitable[None]]  # Raises when the Docker daemon cannot be reached.

__all__ = [
    "DockerProbe",
    "check_docker",
    "check_scratch_root",
    "check_vector_extension",
    "probe_docker",
]


def check_scratch_root(manifest: HiveManifest) -> Check:
    """Check the Hive Stand's scratch root is (or can become) a writable directory with room."""
    name = "scratch root"
    if not manifest.hive_stand.enabled:
        return Check(name=name, outcome=Outcome.SKIPPED, detail="the Hive Stand is disabled")
    root = manifest.resolve_path(manifest.hive_stand.scratch_root)
    existing = _nearest_existing(root)
    if not existing.is_dir():
        return Check(name=name, outcome=Outcome.FAILED, detail=f"{existing} is not a directory")
    try:
        # A real write is the only portable proof: os.access lies about directories on Windows.
        with tempfile.TemporaryFile(dir=existing):
            pass
    except OSError as exc:
        return Check(name=name, outcome=Outcome.FAILED, detail=f"{existing} is not writable: {exc}")
    free_mb = shutil.disk_usage(existing).free // BYTES_PER_MB
    reserve_mb = manifest.hive_stand.disk_reserve_mb
    # Below the reserve every new lease is refused, so the Hive Stand could take no work.
    if free_mb <= reserve_mb:
        detail = f"{free_mb} MB free, at or under the {reserve_mb} MB reserve; leases are refused"
        return Check(name=name, outcome=Outcome.FAILED, detail=detail)
    state = "exists" if existing == root else "will be created"
    return Check(name=name, outcome=Outcome.PASSED, detail=f"{root} {state}, {free_mb} MB free")


def check_vector_extension() -> Check:
    """Check sqlite-vec loads, on a throwaway in-memory database."""
    connection = connect(":memory:")
    try:
        loaded = load_vector_extension(connection)
    finally:
        connection.close()
    if loaded:
        return Check(name="sqlite-vec", outcome=Outcome.PASSED, detail="loads")
    detail = "did not load; the Honey Store ranks vectors in Python instead (slower)"
    return Check(name="sqlite-vec", outcome=Outcome.WARNED, detail=detail)


async def check_docker(manifest: HiveManifest, probe: DockerProbe) -> Check:
    """Check the Docker daemon answers, when the Virtual Cell backend is Docker."""
    name = "docker"
    backend = manifest.virtual_cells.backend
    if backend != "docker":
        detail = f"the Virtual Cell backend is {backend or 'not configured'}"
        return Check(name=name, outcome=Outcome.SKIPPED, detail=detail)
    try:
        # External await: one list call to the local daemon, milliseconds; a hang is the SDK's
        # own timeout. Any failure at all, the SDK's or a missing extra, means Docker is unusable.
        await probe()
    except Exception as exc:
        return Check(name=name, outcome=Outcome.FAILED, detail=f"unreachable: {exc}")
    return Check(name=name, outcome=Outcome.PASSED, detail="the daemon answers")


async def probe_docker() -> None:
    """Connect to the Docker daemon the environment names and list nothing, or raise."""
    # The client imports the optional `docker` SDK only when built, so a Hive without the extra
    # fails here, as a Check, rather than at import.
    await SdkDockerClient().list_containers(_PROBE_LABELS)


def _nearest_existing(path: Path) -> Path:
    """Return `path`, or its nearest ancestor that exists (the scratch root is made on demand)."""
    current = path
    while not current.exists() and current != current.parent:
        current = current.parent
    return current
