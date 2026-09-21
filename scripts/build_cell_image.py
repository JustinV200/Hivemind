"""Build images/base-ubuntu/vm/base-ubuntu.qcow2: the QEMU backend's prebuilt Virtual Cell image.

`hivemind.hive.backends.qemu.QemuCellBackend` never builds a VM image itself (codingrules 8.3:
pure core, effectful edges) -- it only creates a per-Cell copy-on-write overlay backed by whatever
`base_image` its own config names. This script is what produces that base image, offline from any
running Hive, the same relationship `images/base-ubuntu/Dockerfile` has to the Docker backend:

    1. Download the official Ubuntu 24.04 LTS server cloud image (already qcow2-format, despite
       its `.img` extension) and verify its SHA256 against `UBUNTU_CLOUD_IMAGE_SHA256` below.
    2. Copy it to the output path and grow it to `DEFAULT_DISK_SIZE` (`qemu-img resize`; the cloud
       image itself ships small and grows into whatever a Cell's own `disk_bytes` allows).
    3. Boot it exactly once, seeded with a *provisioning* cloud-init document (distinct from
       `hivemind.hive.backends.qemu.cloud_init`'s own per-Cell boot seed: this one has no Cell
       identity yet, since none exists until `QemuCellBackend.provision` makes one) that installs
       Python, `uv`, and the `hivemind`/`waggle` packages into `/opt/hivemind/venv` -- the same
       path `hivemind.hive.backends.qemu.cloud_init._HIVEMIND_IN_CELL_BIN` assumes -- and creates
       the non-root `hive` user, mirroring `images/base-ubuntu/Dockerfile`'s own steps almost line
       for line. The repository's own source tree reaches the provisioning VM through a read-only
       `-virtfs` share (QEMU's own 9p passthrough) rather than a second seed image or a git remote,
       since this dev host has no `git remote` to assume and no reason to build a second ISO just
       to move a tarball nine repository directories over.
    4. The provisioning cloud-init's own `runcmd` powers the VM off once installation finishes;
       this script waits for the QEMU process to exit and reports success.

Codingrules section 4 confines `subprocess` to `hive/backends/*`, the dev sandbox and `scripts/`;
this is one of the scripts that legitimately needs it, for the same two tools
`hivemind.hive.backends.qemu.process_runner` uses (`qemu-img`, `qemu-system-x86_64`).

NOTE: QEMU is not installed on the machine that authored this script (ADR-0026), and this
environment has no network access to fetch Ubuntu's current SHA256SUMS file either. This script
has been written carefully and reviewed by reading, never run -- the same caveat
`images/base-ubuntu/Dockerfile` states for itself. `UBUNTU_CLOUD_IMAGE_SHA256` is a placeholder a
maintainer must replace before a real build (see its own comment); the script refuses to proceed
while it still holds that placeholder, rather than silently skipping verification.

Fits into the Hive:
    Layer: none (a dev/build-time script, not shipped code). Produces
    images/base-ubuntu/vm/base-ubuntu.qcow2, which `hivemind.hive.backends.qemu.QemuCellBackend`'s
    own `base_image` configuration names.

Key invariants:
    - Never proceeds past SHA256 verification on a mismatch, or while the expected digest is
      still the documented placeholder.
    - Degrades with a clear, actionable message (never a bare traceback) when `qemu-img` or
      `qemu-system-x86_64` is not on PATH.

See Also:
    - docs/adr/0026-cell-backends-docker-first-qemu-second.md
    - images/base-ubuntu/vm/README.md for how to run this script and what it produces.
    - images/base-ubuntu/Dockerfile for the container image this script's provisioning steps mirror.
    - hivemind.hive.backends.qemu.process_runner for the same qemu-img/qemu-system-x86_64 tools,
      used at Cell-provisioning time instead of image-build time.
"""

from __future__ import annotations

import argparse
import hashlib
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from collections.abc import Sequence
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUTPUT = REPO_ROOT / "images" / "base-ubuntu" / "vm" / "base-ubuntu.qcow2"

# Ubuntu 24.04 LTS server cloud image: the "releases/24.04/release/" path always serves the latest
# 24.04 point-release build, so both the file and its digest change whenever Ubuntu publishes one.
UBUNTU_CLOUD_IMAGE_URL = (
    "https://cloud-images.ubuntu.com/releases/24.04/release/ubuntu-24.04-server-cloudimg-amd64.img"
)
UBUNTU_CLOUD_IMAGE_SHA256SUMS_URL = (
    "https://cloud-images.ubuntu.com/releases/24.04/release/SHA256SUMS"
)
# TODO(maintainer): replace with the real digest for ubuntu-24.04-server-cloudimg-amd64.img, read
# from UBUNTU_CLOUD_IMAGE_SHA256SUMS_URL immediately before a real build (that file's own digests
# rotate with every point release, so pin this again on every rebuild, not just once).
UBUNTU_CLOUD_IMAGE_SHA256 = "UNVERIFIED-PLACEHOLDER-REPLACE-FROM-SHA256SUMS-BEFORE-BUILDING"

DEFAULT_DISK_SIZE = (
    "8G"  # Generous ceiling; VirtualCellSpec.disk_bytes caps what a Cell actually uses.
)
_HIVE_USER = "hive"
_VENV_PATH = "/opt/hivemind/venv"  # Matches cloud_init.py's own _HIVEMIND_IN_CELL_BIN assumption.
_VIRTFS_MOUNT_TAG = "hivemindsrc"  # Arbitrary but fixed; named in both the -virtfs arg and runcmd.
_PROVISION_MEMORY_MIB = "2048"
_PROVISION_TIMEOUT_S = 1800  # 30 minutes: apt, the uv installer and `uv sync` over a slow mirror.

__all__ = ["main"]


def main(argv: Sequence[str] | None = None) -> int:
    """Build the QEMU backend's prebuilt Cell image end to end; print progress as it goes.

    Args:
        argv: Command-line arguments, excluding the program name. `None` means `sys.argv[1:]`.

    Returns:
        0 on success, 1 if a required tool is missing or the digest was not updated from its
        placeholder, matching every other `scripts/check_*.py` gate's own exit-code convention.
    """
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--output", type=Path, default=DEFAULT_OUTPUT, help="Where to write the qcow2."
    )
    parser.add_argument(
        "--disk-size",
        default=DEFAULT_DISK_SIZE,
        help=f"Final disk size (default {DEFAULT_DISK_SIZE}).",
    )
    args = parser.parse_args(argv)

    tools = _require_tools()
    if tools is None:
        return 1
    qemu_img, qemu_system = tools
    if UBUNTU_CLOUD_IMAGE_SHA256.startswith("UNVERIFIED-PLACEHOLDER"):
        print(
            "UBUNTU_CLOUD_IMAGE_SHA256 is still a placeholder; fetch the real digest from "
            f"{UBUNTU_CLOUD_IMAGE_SHA256SUMS_URL} and update this script before building for real."
        )
        return 1

    try:
        _build(qemu_img, qemu_system, args.output, args.disk_size)
    except (
        ValueError,
        RuntimeError,
        subprocess.CalledProcessError,
        subprocess.TimeoutExpired,
    ) as exc:
        # SAFETY: the top of a CLI command is one of the three places a broad except is allowed
        # (codingrules section 10); every expected failure mode here has a full-sentence message.
        print(f"Build failed: {exc}")
        return 1

    print(f"Built {args.output}")
    return 0


def _require_tools() -> tuple[str, str] | None:
    """Return `(qemu-img, qemu-system-x86_64)` paths, or None (with a message) if one is absent."""
    qemu_img = shutil.which("qemu-img")
    qemu_system = shutil.which("qemu-system-x86_64")
    if qemu_img is None or qemu_system is None:
        # Clear, actionable degrade (roadmap step 5.11's own instruction) rather than a bare
        # FileNotFoundError once subprocess.run tries to exec a missing binary.
        print(
            "qemu-img and qemu-system-x86_64 must both be on PATH to build a Virtual Cell image "
            "(e.g. `apt-get install qemu-utils qemu-system-x86`, `brew install qemu`)."
        )
        return None
    return qemu_img, qemu_system


def _build(qemu_img: str, qemu_system: str, output: Path, disk_size: str) -> None:
    """Download, verify, resize and provision `output`; the body of `main`'s own try block."""
    with tempfile.TemporaryDirectory(prefix="hivemind-build-cell-image-") as raw_tmp_dir:
        tmp_dir = Path(raw_tmp_dir)
        cloud_image = tmp_dir / "ubuntu-24.04-server-cloudimg-amd64.img"
        print(f"Downloading {UBUNTU_CLOUD_IMAGE_URL} ...")
        _download(UBUNTU_CLOUD_IMAGE_URL, cloud_image)
        _verify_sha256(cloud_image, UBUNTU_CLOUD_IMAGE_SHA256)
        output.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(cloud_image, output)
        print(f"Resizing to {disk_size} ...")
        # SAFETY: a fixed argv built from an already-resolved PATH lookup and this function's own
        # parameters, never a shell string (codingrules section 15).
        subprocess.run([qemu_img, "resize", str(output), disk_size], check=True)  # noqa: S603
        seed_iso = _build_provisioning_seed(tmp_dir)
        print("Booting once to provision Python, uv, hivemind/waggle and the hive user ...")
        _boot_and_provision(qemu_system, output, seed_iso)


def _download(url: str, dest: Path) -> None:
    """Stream `url` to `dest`; a plain GET, no auth, matching a public Ubuntu cloud image mirror."""
    # SAFETY: url is always one of this module's own https:// constants, never caller input.
    with urllib.request.urlopen(url) as response, dest.open("wb") as handle:  # noqa: S310
        shutil.copyfileobj(response, handle)


def _verify_sha256(path: Path, expected: str) -> None:
    """Raise ValueError if `path`'s SHA256 does not equal `expected` (case-insensitive)."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    actual = digest.hexdigest()
    if actual.lower() != expected.lower():
        raise ValueError(f"SHA256 mismatch for {path}: expected {expected}, got {actual}.")


def _build_provisioning_seed(tmp_dir: Path) -> Path:
    """Write a one-time provisioning cloud-init NoCloud seed ISO; return its path.

    Distinct from `hivemind.hive.backends.qemu.cloud_init`'s per-Cell seed: this document has no
    Cell identity (none exists at image-build time) and its only job is to leave the image ready
    for `QemuCellBackend` to boot real Cells from later.
    """
    user_data = "\n".join(
        [
            "#cloud-config",
            "packages: [ca-certificates, curl, python3, python3-venv]",
            "runcmd:",
            f"  - useradd --create-home --shell /bin/bash {_HIVE_USER}",
            "  - mkdir -p /var/lib/hivemind/scratch",
            f"  - chown -R {_HIVE_USER}:{_HIVE_USER} /var/lib/hivemind",
            "  - curl -LsSf https://astral.sh/uv/install.sh | sh",
            "  - mkdir -p /mnt/hivemind-src",
            "  - mount -t 9p -o trans=virtio,version=9p2000.L,ro"
            f" {_VIRTFS_MOUNT_TAG} /mnt/hivemind-src",
            "  - cd /mnt/hivemind-src && /root/.local/bin/uv sync --frozen --no-dev"
            " --no-editable --package hivemind",
            f"  - cp -r /mnt/hivemind-src/.venv {_VENV_PATH}",
            f"  - chown -R {_HIVE_USER}:{_HIVE_USER} {_VENV_PATH}",
            "  - umount /mnt/hivemind-src",
            "  - poweroff",
        ]
    )
    meta_data = "instance-id: hivemind-base-ubuntu-provision\nlocal-hostname: hivemind-provision\n"
    (tmp_dir / "user-data").write_text(user_data, encoding="utf-8")
    (tmp_dir / "meta-data").write_text(meta_data, encoding="utf-8")
    tool = next((name for name in ("genisoimage", "mkisofs") if shutil.which(name)), None)
    if tool is None:
        raise RuntimeError(
            "cannot build the provisioning seed image: neither genisoimage nor mkisofs is on "
            "PATH; install one (e.g. `apt-get install genisoimage`)."
        )
    seed_path = tmp_dir / "provision-seed.iso"
    args = [
        tool,
        "-output",
        str(seed_path),
        "-volid",
        "cidata",
        "-joliet",
        "-rock",
        str(tmp_dir / "user-data"),
        str(tmp_dir / "meta-data"),
    ]
    subprocess.run(args, check=True)  # noqa: S603  # SAFETY: a fixed argv, never a shell string.
    return seed_path


def _boot_and_provision(qemu_system_bin: str, image: Path, seed_iso: Path) -> None:
    """Boot `image` once with `seed_iso` attached; block until the guest powers itself off."""
    # security_model=mapped-xattr keeps host file ownership out of the guest's view; readonly=on
    # because this provisioning boot only ever needs to read the repository, never write it.
    virtfs = (
        f"local,path={REPO_ROOT},mount_tag={_VIRTFS_MOUNT_TAG},"
        "security_model=mapped-xattr,readonly=on"
    )
    args = [
        qemu_system_bin,
        "-m",
        _PROVISION_MEMORY_MIB,
        "-accel",
        "tcg",  # Portable default for a one-time build step; speed does not matter here.
        "-drive",
        f"file={image},if=virtio,format=qcow2",
        "-drive",
        f"file={seed_iso},if=virtio,format=raw,media=cdrom",
        "-virtfs",
        virtfs,
        # Unrestricted user-net: this one-time provisioning boot needs apt and the uv installer,
        # unlike a real Cell's own egress policy (hivemind.hive.backends.qemu.network).
        "-netdev",
        "user,id=net0",
        "-device",
        "virtio-net-pci,netdev=net0",
        "-display",
        "none",
        "-nographic",
    ]
    # SAFETY: a fixed argv built from resolved PATH lookups and this function's own parameters,
    # never a shell string (codingrules section 15).
    subprocess.run(args, check=True, timeout=_PROVISION_TIMEOUT_S)  # noqa: S603


if __name__ == "__main__":
    sys.exit(main())
