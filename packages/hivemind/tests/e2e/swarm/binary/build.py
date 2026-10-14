"""Build the single-file Pollen Packet in a Debian 12 container, once for each set of inputs.

Roadmap step 11.7 ships the packet as one PyInstaller file, because a borrowed Raspberry Pi has no
Python 3.12 and no pip. A Linux binary links the C library of the machine that built it, so
`.github/workflows/pollen.yml` builds it inside `python:3.12-bookworm`: Debian 12's glibc (2.36) is
Raspberry Pi OS's, and the oldest the packet supports. This module makes the same file the same
way (the workflow's three commands, in the workflow's image, with the checkout copied in) so a
test can run it on a clean Debian 12 and say whether "`pollen` installs on a clean Raspberry Pi
OS" holds for what CI would ship. A cold build is minutes at worst (an `uv sync` over the network
and a PyInstaller analysis); the result is kept under a key made of everything that goes into it,
so a run after that finds the file and builds nothing.

A build behind a proxy that only the host can reach (an address on its loopback, which a container
on the bridge cannot see) runs on the host network and is handed the proxy and the certificate
that it re-signs traffic with, as `e2e.swarm.image` does for the device image; nothing is baked
into an image, because no image is built, and a proxy that carries a credential is not forwarded.

Fits into the Hive:
    Test infrastructure (codingrules section 14.2), not shipped. Used by `e2e.swarm.binary` and
    the phase 11 packaging scenario; calls `docker` through `e2e.swarm.container`, so it needs a
    docker daemon, the `python:3.12-bookworm` image (pulled by the daemon if absent) and, for a
    cold build, a route to PyPI.

Key invariants:
    - The cache key is a pure function of the sources of `waggle` and `pollen` (the two packages
      the binary is frozen from; what their wheels would hold), `hivemind`'s project file, the
      lock, the build script, the root project file, the recipe and the image name. A change to
      `hivemind`'s code does not rebuild: the binary excludes it.
    - A failed or interrupted build stores nothing: the file reaches the cache by one rename of a
      directory that is complete, so a later run never finds half of one.
    - The build container is always removed, and a build that outlives `BUILD_TIMEOUT_S` raises
      naming the limit rather than hanging.
    - Old entries are never pruned; they are safe to delete (`rm -r` the cache directory).

See Also:
    - .github/workflows/pollen.yml for the recipe this repeats, and scripts/build_pollen.py for
      what it runs.
    - e2e/swarm/image.py for the proxy handling this follows and the cache directory it shares.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from e2e.swarm.container import CommandError, Container, ContainerSpec, Janitor, docker

BINARY_NAME = "pollen"  # The file's name in the cache and in the container: the operator's command.
BUILD_IMAGE = (
    "python:3.12-bookworm"  # The workflow's BUILD_IMAGE: Debian 12, Python 3.12, binutils.
)
CACHE_VARIABLE = "HIVEMIND_TEST_CACHE_DIR"  # The variable `e2e.swarm.image` reads for its cache.
CACHE_SUBDIRECTORY = "pollen-binary"  # Beside the device image's downloads, one directory per key.
BUILD_TIMEOUT_S = 1200.0  # A cold build: about a minute here; minutes on a slow link or a Pi.
COPY_TIMEOUT_S = 120.0  # `docker cp` of a 20 MB source tree in, or a 40 MB binary out.
OUTPUT_TAIL_LINES = 40  # How much of a failed build's own output goes into the error.
REPO_ROOT = (
    Path(__file__).resolve().parents[6]
)  # tests/e2e/swarm/binary/build.py up to the checkout.
# What the freeze reads, relative to the checkout: the workspace's roots, the lock that pins every
# wheel, the build script and the interpreter the lock was resolved for.
ROOT_FILES = ("pyproject.toml", "uv.lock", ".python-version", "scripts/build_pollen.py")
FROZEN_PACKAGES = ("waggle", "pollen")  # The workspace members the binary is made of.
HIVE_PROJECT_FILE = (
    "packages/hivemind/pyproject.toml"  # In the lock's workspace, not in the binary.
)
# The workflow's build step: the same three commands and the same environment, so the file built
# here is the file CI uploads. /src is the checkout and /out is where the binary is left.
BUILD_SCRIPT = """\
set -euo pipefail
cd /src
mkdir -p /out
pip install --quiet --no-cache-dir --disable-pip-version-check --root-user-action=ignore uv
uv sync --frozen --group pollen-build
uv run --frozen --group pollen-build python scripts/build_pollen.py --output-dir /out
"""
BUILD_ENVIRONMENT = {
    # Keeps the virtual environment out of /src; a path inside the throwaway container.
    "UV_PROJECT_ENVIRONMENT": "/tmp/build-venv",  # noqa: S108  # SAFETY: container-internal.
    "UV_PYTHON_PREFERENCE": "only-system",  # The image's own Python, built against its glibc.
    "UV_PYTHON_DOWNLOADS": "never",  # A downloaded Python would be built against another glibc.
}
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
CA_BUNDLE_VARIABLES = ("SSL_CERT_FILE", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE")
CA_TARGET_VARIABLES = ("SSL_CERT_FILE", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE", "PIP_CERT")
CONTAINER_CA = "/tmp/proxy-ca.crt"  # noqa: S108  # SAFETY: a path inside the build container.

__all__ = [
    "BINARY_NAME",
    "BUILD_IMAGE",
    "BUILD_TIMEOUT_S",
    "BuildNetwork",
    "binary_digest",
    "build_binary",
    "build_network",
    "cache_dir",
    "frozen_inputs",
    "stage_files",
]


@dataclass(frozen=True, slots=True)
class BuildNetwork:
    """How the build container reaches PyPI: its network, its variables and a CA file to copy."""

    network: str | None = None  # `host` behind a loopback proxy; None for the default bridge.
    environment: Mapping[str, str] = field(default_factory=dict)  # Proxy and certificate settings.
    ca_bundle: Path | None = None  # The host's file to copy to `CONTAINER_CA`, if there is one.


def build_binary(root: Path = REPO_ROOT) -> Path:
    """Return the single-file packet built from `root`, building it first if its key is new.

    Args:
        root: The checkout to build from; this tree by default.

    Returns:
        The path of an executable file in the cache, the same bytes every time `root`'s inputs
        are the same.

    Raises:
        CommandError: The build container could not be started, a copy failed, or the build
            failed or outlived `BUILD_TIMEOUT_S`; the message carries the end of the build's
            own output.
    """
    entry = cache_dir() / binary_digest(root)
    binary = entry / BINARY_NAME
    # The same inputs were built before: the whole cold cost is paid once per change.
    if binary.is_file():
        return binary
    entry.parent.mkdir(parents=True, exist_ok=True)
    # Built beside the final name and renamed in one step, so a build that is cut short (or a
    # second process doing the same) never leaves a directory that looks complete.
    partial = Path(tempfile.mkdtemp(prefix=f"{entry.name}.", dir=entry.parent))
    try:
        _build_into(partial / BINARY_NAME, root)
        try:
            partial.rename(entry)
        except OSError:
            # Another process stored this key first; its file is the same, so ours is surplus.
            if not binary.is_file():
                raise
            shutil.rmtree(partial)
    except BaseException:
        shutil.rmtree(partial, ignore_errors=True)
        raise
    return binary


def cache_dir() -> Path:
    """Where built binaries are kept: the shared variable's directory, else the user's cache."""
    if os.environ.get(CACHE_VARIABLE):
        return Path(os.environ[CACHE_VARIABLE]) / CACHE_SUBDIRECTORY
    base = os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache"
    return Path(base) / "hivemind-tests" / CACHE_SUBDIRECTORY


def binary_digest(root: Path = REPO_ROOT) -> str:
    """Hash everything that goes into the binary into the key it is cached under.

    Args:
        root: The checkout to hash.

    Returns:
        Sixteen hex digits: the same for two trees whose inputs are byte for byte equal.
    """
    digest = hashlib.sha256()
    for relative in frozen_inputs(root):
        data = (root / relative).read_bytes()
        # Name and length before the bytes, so moving a byte between two files changes the key.
        digest.update(f"{relative.as_posix()}\0{len(data)}\0".encode())
        digest.update(data)
    # The recipe, the image and the environment are inputs as much as the files are.
    recipe = (BUILD_IMAGE, BUILD_SCRIPT, sorted(BUILD_ENVIRONMENT.items()))
    digest.update(repr(recipe).encode())
    return digest.hexdigest()[:16]


def frozen_inputs(root: Path = REPO_ROOT) -> list[Path]:
    """List the files the binary depends on, relative to `root`, sorted.

    Args:
        root: The checkout to list.

    Returns:
        The root project files, the lock, the build script, `hivemind`'s project file and every
        file of `waggle` and `pollen` outside their tests and bytecode.
    """
    files = [Path(name) for name in (*ROOT_FILES, HIVE_PROJECT_FILE)]
    for package in FROZEN_PACKAGES:
        files += _package_files(root, package)
    return sorted(files)


def stage_files(root: Path = REPO_ROOT) -> list[Path]:
    """List the files the build container needs: the inputs, and the rest of the workspace.

    `uv sync` installs every member of the workspace, `hivemind` included, so the member must be
    there even though none of its code is frozen.

    Args:
        root: The checkout to list.

    Returns:
        Paths relative to `root`, sorted.
    """
    return sorted({*frozen_inputs(root), *_package_files(root, "hivemind")})


def build_network() -> BuildNetwork:
    """Decide how the build container reaches PyPI from the host's proxy settings.

    Returns:
        A host-network container with the proxy and its certificate when the host's proxy is on
        its own loopback and carries no credential; the default network otherwise. A credential
        in a proxy address would be recorded in the container's configuration, so it is not
        forwarded.
    """
    proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy") or ""
    address = urlsplit(proxy)
    if address.hostname not in LOOPBACK_HOSTS or address.username or address.password:
        return BuildNetwork()
    environment = {"HTTPS_PROXY": proxy, "https_proxy": proxy}
    bundle = _ca_bundle()
    if bundle is not None:
        # The proxy re-signs TLS, so every tool in the build has to trust its certificate.
        environment.update(dict.fromkeys(CA_TARGET_VARIABLES, CONTAINER_CA))
    return BuildNetwork(network="host", environment=environment, ca_bundle=bundle)


def _ca_bundle() -> Path | None:
    """Return the host's CA bundle file named by the first variable that points at one."""
    for variable in CA_BUNDLE_VARIABLES:
        bundle = os.environ.get(variable)
        if bundle and Path(bundle).is_file():
            return Path(bundle)
    return None


def _package_files(root: Path, package: str) -> list[Path]:
    """List a workspace member's files, relative to `root`, less its tests and bytecode."""
    base = root / "packages" / package
    kept = []
    for path in sorted(base.rglob("*")):
        relative = path.relative_to(base)
        # Tests are never frozen, and bytecode is rebuilt: neither may change the key.
        if path.is_file() and relative.parts[0] != "tests" and "__pycache__" not in relative.parts:
            kept.append(path.relative_to(root))
    return kept


def _build_into(destination: Path, root: Path) -> None:
    """Build in a throwaway container and copy the binary to `destination`.

    Raises:
        CommandError: As `build_binary`.
    """
    janitor = Janitor()
    try:
        with tempfile.TemporaryDirectory(prefix="pollen-build-") as scratch:
            # Copied in, not mounted: nothing the build writes may land in the developer's tree,
            # and a daemon that is not on this machine can still be used.
            stage = Path(scratch) / "src"
            for relative in stage_files(root):
                (stage / relative).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(root / relative, stage / relative)
            access = build_network()
            spec = ContainerSpec(
                image=BUILD_IMAGE,
                command=("sleep", "infinity"),  # The build runs as an exec, so its status is seen.
                environment={**BUILD_ENVIRONMENT, **access.environment},
                network=access.network,
            )
            container = janitor.start(spec)
            _copy_in(container, stage, access)
            _run_script(container)
            source = f"{container.name}:/out/{BINARY_NAME}"
            docker("cp", source, str(destination), timeout_s=COPY_TIMEOUT_S)
    finally:
        janitor.close()
    destination.chmod(0o755)


def _copy_in(container: Container, stage: Path, access: BuildNetwork) -> None:
    """Put the staged checkout (and the proxy's certificate, if any) into the container."""
    docker("cp", f"{stage}/.", f"{container.name}:/src", timeout_s=COPY_TIMEOUT_S)
    if access.ca_bundle is not None:
        target = f"{container.name}:{CONTAINER_CA}"
        docker("cp", str(access.ca_bundle), target, timeout_s=COPY_TIMEOUT_S)


def _run_script(container: Container) -> None:
    """Run the workflow's build commands in the container; raise with the end of the output."""
    # Minutes at worst on a cold cache: `uv sync` downloads the locked wheels and PyInstaller
    # analyses the packet. Past BUILD_TIMEOUT_S the call raises and the janitor kills the rest.
    built = docker(
        "exec", container.name, "bash", "-c", BUILD_SCRIPT, timeout_s=BUILD_TIMEOUT_S, check=False
    )
    if built.exit_code != 0:
        tail = "\n".join((built.stdout + built.stderr).strip().splitlines()[-OUTPUT_TAIL_LINES:])
        raise CommandError(f"The pollen binary did not build (exit {built.exit_code}):\n{tail}")
