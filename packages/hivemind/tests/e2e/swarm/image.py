"""Build the wheelhouse and the device image that the swarm tests run Pollen Packets in.

The "second machine" of the swarm tests is a container whose packet and Hive runtime come from
wheels built from this tree, installed offline from a wheelhouse, the way a real device installs
from a source its operator configured. `wheels` builds the three workspace wheels (`uv build`),
takes the exact third-party versions hivemind pins (`uv export` of the lockfile, limited to what
applies to CPython 3.12 on x86-64 Linux, the device) and downloads their wheels (`pip download`
for those tags; the downloads are kept in a cache keyed by the pins, so only a changed lockfile
downloads again). `device_image` puts the wheelhouse and `device/Dockerfile` into a build context,
tags the image by a hash of both, and builds it only when no image has that tag: a change to a
package or to the Dockerfile makes a new tag, and anything else reuses the old image. A variant
is the same device with more apt packages installed (`device_image(("xvfb", ...))`, the X server
and tools of a desktop for the Exoskeleton tests): the package names are part of the hash, so each
variant has a tag of its own and the plain image's is unchanged by them.
`HIVEMIND_TEST_DEVICE_IMAGE` names an image to use instead, built some other way.

A build behind a proxy that only the host can reach (an address on its loopback, which a
container's own network cannot see) runs on the host network and hands the proxy and its
certificate to the Dockerfile for its package step, so the same code builds on a laptop, in CI and
in a sandbox with an egress policy.

Fits into the Hive:
    Test infrastructure (codingrules section 14.3), not shipped. Used by `e2e.swarm.enrolled` and
    the swarm integration and end-to-end tests; calls `uv` and `docker` through
    `e2e.swarm.container.run`, so it needs both on the path and a docker daemon (Docker 23 or
    later, whose builder understands the Dockerfile's `RUN --mount`).

Key invariants:
    - The image tag is a pure function of the wheels, the Dockerfile and the extra packages, so
      two machines that build the same tree get the same tag, and a build never happens twice for
      one tree.
    - A failed build or download raises with the tail of the tool's own output, never an empty
      "failed"; nothing half-built is ever cached or tagged.
    - No credential goes into the image: a proxy that needs one is not forwarded, and the
      certificate of a proxy is mounted for the package layer's one `RUN`, never stored.

See Also:
    - e2e/swarm/device/Dockerfile for what is built.
    - docs/waggle/gateway-contract.md section 3 for what a device needs from its platform.
"""

from __future__ import annotations

import functools
import hashlib
import os
import re
import shutil
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from e2e.swarm.container import LABEL, LABEL_VALUE, CommandError, daemon_answers, docker, run
from packaging.requirements import Requirement

IMAGE_VARIABLE = "HIVEMIND_TEST_DEVICE_IMAGE"  # An image to use as it is, instead of building.
CACHE_VARIABLE = "HIVEMIND_TEST_CACHE_DIR"  # Where downloaded wheels are kept between runs.
REPOSITORY = "hivemind-test-device"
REPO_ROOT = Path(__file__).resolve().parents[5]  # tests/e2e/swarm/image.py up to the checkout.
DOCKERFILE = Path(__file__).resolve().parent / "device" / "Dockerfile"
UV_TIMEOUT_S = 300.0  # Building three wheels or exporting the lockfile takes seconds.
DOWNLOAD_TIMEOUT_S = 900.0  # Forty-odd wheels, once per lockfile, over whatever network there is.
BUILD_TIMEOUT_S = 1800.0  # An apt layer and a pip install; seconds when warm, minutes when cold.
OUTPUT_TAIL_LINES = 40  # How much of a failing tool's output goes into the error.
PYTHON_VERSION = "3.12"  # The device's interpreter: Ubuntu 24.04's own.
# Every wheel tag a device with glibc 2.39 accepts; pip takes the best match among them.
PLATFORMS = (
    "manylinux2014_x86_64",
    "manylinux_2_17_x86_64",
    "manylinux_2_28_x86_64",
    "manylinux_2_34_x86_64",
    "manylinux_2_35_x86_64",
)
# The marker environment of the device, to keep only the pins that apply there.
DEVICE_ENVIRONMENT = {
    "python_version": PYTHON_VERSION,
    "python_full_version": "3.12.3",
    "implementation_name": "cpython",
    "implementation_version": "3.12.3",
    "sys_platform": "linux",
    "platform_system": "Linux",
    "platform_machine": "x86_64",
    "platform_python_implementation": "CPython",
    "os_name": "posix",
    "platform_release": "",
    "platform_version": "",
    "extra": "",
}
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
# An apt package name (Debian policy 5.6.1): the build argument is expanded by a shell, so nothing
# else is let through.
PACKAGE_NAME = re.compile(r"^[a-z0-9][a-z0-9+.-]+$")
CA_VARIABLES = ("SSL_CERT_FILE", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE")

__all__ = [
    "IMAGE_VARIABLE",
    "ImageUnavailableError",
    "device_image",
    "device_image_or_skip",
    "device_pins",
    "image_digest",
    "proxy_flags",
    "wheelhouse",
]


class ImageUnavailableError(Exception):
    """`HIVEMIND_TEST_DEVICE_IMAGE` names an image that the docker daemon does not have."""


@functools.cache
def device_image(extra_packages: tuple[str, ...] = ()) -> str:
    """Return the tag of the device image for this tree, building it first if need be.

    Args:
        extra_packages: apt packages to install besides the plain device's, such as an X server
            for a test of the Exoskeleton; none for the plain device.

    Returns:
        The image to start device containers from; `HIVEMIND_TEST_DEVICE_IMAGE` if set (which
        is then used as it is, whatever packages were asked for).

    Raises:
        ImageUnavailableError: `HIVEMIND_TEST_DEVICE_IMAGE` is set to an image docker lacks.
        ValueError: A package name is not an apt package name.
        CommandError: A wheel could not be built or downloaded, or the image did not build.
    """
    override = os.environ.get(IMAGE_VARIABLE)
    if override:
        if not _image_exists(override):
            raise ImageUnavailableError(f"{IMAGE_VARIABLE}={override} is not an image here.")
        return override
    bad = [name for name in extra_packages if PACKAGE_NAME.match(name) is None]
    if bad:
        raise ValueError(f"Not apt package names: {', '.join(bad)}.")
    with tempfile.TemporaryDirectory(prefix="hivemind-device-image-") as scratch:
        context = Path(scratch)
        wheelhouse(context / "wheelhouse")
        digest = image_digest(context / "wheelhouse", DOCKERFILE, extra_packages)
        tag = f"{REPOSITORY}:{digest}"
        if not _image_exists(tag):
            _build(tag, context, extra_packages)
    return tag


def device_image_or_skip(extra_packages: tuple[str, ...] = ()) -> str:
    """Return the device image's tag, or skip the test when it cannot be had.

    The one place a scenario's fixture learns that a second machine is not to be had here: no
    docker daemon answers, or `HIVEMIND_TEST_DEVICE_IMAGE` names an image the daemon lacks.

    Args:
        extra_packages: apt packages to install besides the plain device's; see `device_image`.

    Returns:
        The image to start device containers from.
    """
    if not daemon_answers():
        pytest.skip("no Docker daemon reachable")
    try:
        return device_image(extra_packages)
    except ImageUnavailableError as missing:
        pytest.skip(str(missing))


def wheelhouse(destination: Path) -> Path:
    """Collect every wheel the device installs from into `destination`.

    The three workspace wheels are built fresh from this tree; the third-party wheels come from
    the cache, downloaded first if the lockfile's pins are new to it.

    Args:
        destination: A directory that does not exist yet.

    Returns:
        `destination`, now holding the wheels.

    Raises:
        CommandError: A wheel could not be built or downloaded.
    """
    run(
        ("uv", "build", "--wheel", "--all-packages", "--out-dir", str(destination)),
        UV_TIMEOUT_S,
        cwd=REPO_ROOT,
    ).require()
    (destination / ".gitignore").unlink(missing_ok=True)  # uv's note to version control; no wheel.
    for wheel in _third_party_wheels().glob("*.whl"):
        shutil.copy2(wheel, destination / wheel.name)
    return destination


def _third_party_wheels() -> Path:
    """Return the cached directory of third-party wheels for the lockfile's pins, filling it."""
    pins = device_pins()
    key = hashlib.sha256("\n".join((PYTHON_VERSION, *PLATFORMS, *pins)).encode()).hexdigest()[:16]
    cache = _cache_dir()
    cached = cache / f"third-party-{key}"
    if cached.is_dir():
        return cached
    cache.mkdir(parents=True, exist_ok=True)
    # Downloaded beside the final name and renamed in one step, so a download that is cut short
    # (or a second process doing the same) never leaves a directory that looks complete.
    partial = Path(tempfile.mkdtemp(prefix=f"third-party-{key}.", dir=cache))
    pinned = partial / "pins.txt"
    pinned.write_text("\n".join(pins) + "\n", encoding="utf-8")
    # Only wheels, for the device's tags and not this machine's, with the pins taken as they are.
    download = ["uv", "run", "--no-project", "--with", "pip", "python", "-m", "pip", "download"]
    download += [
        "--only-binary=:all:",
        "--python-version",
        PYTHON_VERSION,
        "--implementation",
        "cp",
    ]
    download += ["--no-deps", "--requirement", str(pinned), "--dest", str(partial)]
    for tag in PLATFORMS:
        download += ["--platform", tag]
    run(download, DOWNLOAD_TIMEOUT_S, cwd=REPO_ROOT).require()
    pinned.unlink()
    try:
        partial.rename(cached)
    except OSError:
        shutil.rmtree(partial)  # Another process finished first; its directory is the same.
    return cached


def device_pins() -> list[str]:
    """Return the lockfile's pinned requirements that apply to the device, sorted.

    Raises:
        CommandError: The lockfile could not be exported.
    """
    export = ["uv", "export", "--frozen", "--no-hashes", "--no-emit-workspace", "--package"]
    export += ["hivemind", "--no-dev", "--format", "requirements-txt", "--no-header"]
    exported = run([*export, "--no-annotate"], UV_TIMEOUT_S, cwd=REPO_ROOT).require()
    pins = []
    for line in exported.stdout.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        requirement = Requirement(line)
        applies = requirement.marker is None or requirement.marker.evaluate(DEVICE_ENVIRONMENT)
        if applies:
            pins.append(f"{requirement.name}=={next(iter(requirement.specifier)).version}")
    return sorted(pins)


def _cache_dir() -> Path:
    """Where downloaded wheels are kept: the variable's directory, else the user's cache."""
    if os.environ.get(CACHE_VARIABLE):
        return Path(os.environ[CACHE_VARIABLE])
    base = os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache"
    return Path(base) / "hivemind-tests" / "device-image"


def image_digest(wheels: Path, dockerfile: Path, extra_packages: tuple[str, ...] = ()) -> str:
    """Hash the Dockerfile, every wheel (name and bytes) and the extra packages into an identity."""
    digest = hashlib.sha256(dockerfile.read_bytes())
    for wheel in sorted(wheels.glob("*.whl")):
        digest.update(wheel.name.encode())
        digest.update(wheel.read_bytes())
    if extra_packages:  # Only a variant adds to the hash, so the plain image keeps its own.
        digest.update(b"\0extra-packages\0" + " ".join(sorted(extra_packages)).encode())
    return digest.hexdigest()[:16]


def _image_exists(tag: str) -> bool:
    """Whether the docker daemon has an image with this tag."""
    return docker("image", "inspect", tag, check=False).exit_code == 0


def _build(tag: str, context: Path, extra_packages: tuple[str, ...] = ()) -> None:
    """Build the image from the Dockerfile and the wheelhouse already in `context`.

    Raises:
        CommandError: The build failed; the message carries the end of its output.
    """
    shutil.copy2(DOCKERFILE, context / "Dockerfile")
    (
        context / "ca"
    ).mkdir()  # The Dockerfile mounts it; it holds a certificate only behind a proxy.
    args = ["build", "--tag", tag, "--label", f"{LABEL}={LABEL_VALUE}", *proxy_flags(context)]
    if extra_packages:
        args += ["--build-arg", f"EXTRA_PACKAGES={' '.join(extra_packages)}"]
    built = docker(*args, str(context), timeout_s=BUILD_TIMEOUT_S, check=False)
    if built.exit_code != 0:
        tail = "\n".join(built.stderr.strip().splitlines()[-OUTPUT_TAIL_LINES:])
        raise CommandError(f"The device image did not build (exit {built.exit_code}):\n{tail}")


def proxy_flags(context: Path) -> list[str]:
    """Return the build flags for a proxy that only the host can reach, else none.

    A proxy on the host's loopback is invisible from a container's network, so the build shares
    the host's, and the Dockerfile is told the proxy to use and given the certificate that it
    re-signs traffic with (copied into `context/ca`, mounted for one layer, never stored). A build
    argument is recorded in the image's history, so a proxy address that carries a user name or
    password is not forwarded.
    """
    proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy") or ""
    address = urlsplit(proxy)
    if address.hostname not in LOOPBACK_HOSTS or address.username or address.password:
        return []
    for variable in CA_VARIABLES:
        bundle = os.environ.get(variable)
        if bundle and Path(bundle).is_file():
            shutil.copy2(bundle, context / "ca" / "proxy.crt")
            break
    return ["--network", "host", "--build-arg", f"APT_PROXY={proxy}"]
