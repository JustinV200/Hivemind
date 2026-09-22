# base-ubuntu/vm: the QEMU backend's prebuilt Virtual Cell image

`hivemind.hive.backends.qemu.QemuCellBackend` (roadmap step 5.11) provisions every VM as a
copy-on-write overlay backed by one prebuilt qcow2, `base-ubuntu.qcow2`, built here. This mirrors
`images/base-ubuntu/Dockerfile`'s relationship to the Docker backend: same non-root `hive` user,
same `hivemind-in-cell` entry point, same `HIVEMIND_*` runtime contract (see the parent
directory's own `README.md`), different infrastructure underneath.

## `base-ubuntu.qcow2` is git-ignored

The qcow2 itself is a multi-gigabyte binary artefact that regenerates deterministically from this
directory's own build script and the pinned Ubuntu release; it is **never committed**. The
repository's root `.gitignore` excludes `images/*/vm/*.qcow2` (added alongside this README).

## How the image is built

`scripts/build_cell_image.py`, run from the repository root:

```sh
uv run python scripts/build_cell_image.py
```

1. Downloads the official Ubuntu 24.04 LTS server cloud image (`UBUNTU_CLOUD_IMAGE_URL`, already
   qcow2-format despite its `.img` extension) and verifies its SHA256 against
   `UBUNTU_CLOUD_IMAGE_SHA256`, a constant a maintainer must keep current -- see that constant's
   own comment for exactly where to read the real digest from before every rebuild. The script
   refuses to proceed while the *effective* digest is still the bundled placeholder; pass the real
   one without editing the file either with `--sha256 <digest>` or by setting
   `HIVEMIND_QEMU_BASE_IMAGE_SHA256` (`--sha256` wins when both are given), e.g.:

   ```sh
   uv run python scripts/build_cell_image.py --sha256 <digest-from-SHA256SUMS>
   # or
   HIVEMIND_QEMU_BASE_IMAGE_SHA256=<digest-from-SHA256SUMS> uv run python scripts/build_cell_image.py
   ```
2. Copies it to `base-ubuntu.qcow2` and grows it to `--disk-size` (default `8G`; a Cell's own
   `VirtualCellSpec.disk_bytes` is the real ceiling `QemuCellBackend` enforces per Cell).
3. Boots it once, seeded with a one-time provisioning cloud-init document, to install Python,
   `uv`, and the `hivemind`/`waggle` packages into `/opt/hivemind/venv`, and to create the
   non-root `hive` user -- the same steps `images/base-ubuntu/Dockerfile` runs for the container
   image, reached here through a read-only QEMU `-virtfs` share of the repository rather than a
   Docker build context.
4. The provisioning cloud-init's own `runcmd` powers the VM off when installation finishes; the
   script waits for that and reports the finished image's path.

The script degrades with a clear, actionable message (never a bare traceback) when `qemu-img`,
`qemu-system-x86_64`, or an ISO-building tool (`genisoimage`/`mkisofs`, for the one-time
provisioning seed) is not on `PATH`.

## Building and testing this image

QEMU is not installed on the machine that authored this image and its build script (ADR-0026);
both have been written carefully and reviewed by reading, never run -- the same caveat
`images/base-ubuntu/Dockerfile` states for itself. A QEMU-enabled CI runner or developer machine
(a later roadmap step) is what must, at minimum:

- Run `scripts/build_cell_image.py` after replacing `UBUNTU_CLOUD_IMAGE_SHA256` with the real
  digest, and confirm it produces a bootable `base-ubuntu.qcow2`.
- Boot an overlay of that image directly with `qemu-img create -b` plus a per-Cell seed from
  `hivemind.hive.backends.qemu.cloud_init`, exactly as `QemuCellBackend.provision` does, and
  confirm the same things `images/base-ubuntu/README.md` asks of the container image: a signed
  `CellReady` then a `CellHeartbeat`, the process running as the non-root `hive` user, and no port
  listening inside the guest.
- Confirm `scripts/check_no_model_ids.py` and the other `scripts/check_*.py` hygiene gates pass
  against this directory (they already do as authored; this is a regression check for future
  edits).

## Not yet in this image

- **Multiple named images.** `QemuCellBackend` currently backs every Cell with this one qcow2
  regardless of `VirtualCellSpec.image`; `desktop-ubuntu`/`night-veil-ubuntu` VM images (roadmap
  steps 6.1, 5.3a) are future work, mirroring the container images of the same names.
- **A CI-verified build.** See "Building and testing this image" above.
