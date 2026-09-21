"""Render cloud-init's NoCloud user-data and meta-data documents for one QEMU Virtual Cell.

Pure functions only (codingrules 8.3): `render_user_data`/`render_meta_data` take a
`hivemind.hive.backends.bootstrap.CellBootstrap` (the fresh per-Cell identity every backend mints,
roadmap step 5.2) and return plain YAML text, with no I/O of their own --
`hivemind.hive.backends.qemu.process_runner.ProcessQemuRunner` is what actually writes these
strings to a NoCloud seed image (`QemuRunnerPort.write_seed_image`). The cloud-init NoCloud
datasource (https://cloudinit.readthedocs.io/en/latest/reference/datasources/nocloud.html) is how
a QEMU guest with no network-reachable metadata service still gets configured at first boot: QEMU
attaches a small ISO9660 volume labelled `cidata` holding exactly two files, `user-data` (a
`#cloud-config` document) and `meta-data`, and cloud-init inside the guest reads them from the
attached disk.

What this module's `user-data` document does, matching ADR-0027's "the image's entry point starts
a Warden, and the Warden dials out" almost exactly, but for a VM image instead of a container:

    1. Writes this Cell's own private signing key to a 0600 file the `hive` user owns
       (`SIGNING_KEY_FILE_PATH`) -- never inlined into the environment file, the kernel command
       line, or `meta-data` (codingrules section 13/15: secrets are never logged or embedded in
       something that ends up readable by every process on the box).
    2. Writes every other `HIVEMIND_*` variable, `HIVEMIND_CELL_SIGNING_KEY_FILE` pointing at that
       key file (never `HIVEMIND_CELL_SIGNING_KEY` itself), to a root-owned 0600 env file
       (`ENV_FILE_PATH`) `systemd` reads as the unit's own `EnvironmentFile` -- systemd (running as
       root) reads this file before it drops privileges to the `hive` user, so root-owned 0600 is
       exactly as private as the Docker backend's own container-scoped environment, never weaker.
    3. Writes a systemd unit (`SYSTEMD_UNIT_PATH`) that runs `hivemind-in-cell` as the non-root
       `hive` user (mirroring images/base-ubuntu's own non-root `USER hive`), and prints
       `READINESS_MARKER` to the VM's first serial port the instant the unit starts -- before
       `hivemind-in-cell` itself has even connected out -- so
       `hivemind.hive.backends.qemu.backend.QemuCellBackend` has a readiness signal that does not
       depend on the Queen side existing yet (the same reason `hivemind.hive.backends.bootstrap.
       ReadinessGate` is deliberately still just a seam, roadmap step 5.2's own docstring).
    4. Enables and starts that unit.

`render_user_data` accepts an optional `queen_waggle_url_override`: `hivemind.hive.backends.qemu.
network.plan_network` sometimes has to rewrite the URL a Cell must actually dial (its own module
docstring explains exactly when and why), and this is the one seam that lets it without this
module needing to import `hivemind.hive.backends.qemu.network` back (which would cycle the two
modules, since `network.py`'s own relay command needs nothing from here).

Fits into the Hive:
    Layer 3 (sources of Cells), inside `hivemind.hive.backends.qemu`. Called by
    `hivemind.hive.backends.qemu.backend.QemuCellBackend`, through
    `hivemind.hive.backends.qemu.process_runner.ProcessQemuRunner.write_seed_image`. Calls into
    `hivemind.hive.backends.bootstrap` (CellBootstrap) only.

Key invariants:
    - `render_user_data`'s output never contains `bootstrap.private_key_hex`'s raw value under any
      key but the dedicated signing-key file's own `content:` block; the env file always carries
      `HIVEMIND_CELL_SIGNING_KEY_FILE`, never `HIVEMIND_CELL_SIGNING_KEY`.
    - Every `write_files` entry this module renders sets an explicit `owner` and `permissions`;
      nothing is left at cloud-init's own default (world-readable) mode.
    - `render_user_data`/`render_meta_data` perform no I/O and raise nothing: `CellBootstrap` is
      already validated by the time it reaches here (`mint_cell_bootstrap`'s own contract).

See Also:
    - docs/adr/0027-virtual-cells-connect-outbound-only-and-boot-a-warden.md for the HIVEMIND_*
      contract this module's `write_files` implements for a VM instead of a container.
    - images/base-ubuntu/README.md "Runtime configuration" for the exact HIVEMIND_* variable
      names and the two `..._FILE` variants this module always prefers for the signing key.
    - hivemind.hive.backends.bootstrap for CellBootstrap, this module's one input type.
    - hivemind.hive.backends.qemu.network for plan_network, the one caller of
      `queen_waggle_url_override`.
    - hivemind.hive.backends.qemu.process_runner for ProcessQemuRunner, which turns this module's
      output into a real NoCloud seed image.
"""

from __future__ import annotations

from hivemind.hive.backends.bootstrap import CellBootstrap

# The exact HIVEMIND_* variable name images/base-ubuntu's entry point (and
# hivemind.manifest.env.read_in_cell_env) reads for the Queen's own URL; a public, documented
# constant here (not a private symbol borrowed from hivemind.hive.backends.bootstrap) so
# network.py's URL override never needs to import that module's own private constants.
ENV_QUEEN_WAGGLE_URL_KEY = "HIVEMIND_QUEEN_WAGGLE_URL"

# Where this module's write_files entries land inside the guest; the signing key is owned by the
# non-root `hive` user (built into the base qcow2, see images/base-ubuntu/vm/README.md) because
# hivemind-in-cell itself opens this path directly (hivemind.cli.in_cell.config._signing_key_bytes)
# running as that user; the env file is root-owned because only systemd (running as root, before it
# drops privileges to User=hive) ever reads it, exactly like a root-owned Docker env would be no
# more exposed than the container's own boundary.
SIGNING_KEY_FILE_PATH = "/etc/hivemind/cell_signing_key"
ENV_FILE_PATH = "/etc/hivemind/env"
SYSTEMD_UNIT_PATH = "/etc/systemd/system/hivemind-in-cell.service"
SYSTEMD_UNIT_NAME = "hivemind-in-cell.service"

# The exact line QemuCellBackend polls the serial console for (hivemind.hive.backends.qemu.backend's
# own readiness wait); fixed so a snapshot test can pin it and so the wait never has to guess at a
# systemd startup message's own wording.
READINESS_MARKER = "HIVEMIND-CELL-READY"

# Where the base qcow2 (images/base-ubuntu/vm/README.md) installs the venv `hivemind-in-cell`
# lives, matching images/base-ubuntu/Dockerfile's own /opt/hivemind/venv layout for the container
# image, so the two images agree on where the entry point's console script ends up.
_HIVEMIND_IN_CELL_BIN = "/opt/hivemind/venv/bin/hivemind-in-cell"

__all__ = [
    "ENV_FILE_PATH",
    "ENV_QUEEN_WAGGLE_URL_KEY",
    "READINESS_MARKER",
    "SIGNING_KEY_FILE_PATH",
    "SYSTEMD_UNIT_NAME",
    "SYSTEMD_UNIT_PATH",
    "render_meta_data",
    "render_user_data",
]


def render_meta_data(bootstrap: CellBootstrap) -> str:
    """Render the NoCloud `meta-data` document: just enough for cloud-init to accept the seed.

    Args:
        bootstrap: This Cell's freshly minted identity.

    Returns:
        A two-line YAML document: `instance-id` (this Cell's id, so a re-seeded VM with the same
        id never re-runs first-boot modules) and `local-hostname` (a DNS-safe form of the same
        id, since a Virtual Cell is never addressed by hostname, ADR-0027).
    """
    hostname = str(bootstrap.cell_id).replace("_", "-")
    return f"instance-id: {bootstrap.cell_id}\nlocal-hostname: {hostname}\n"


def render_user_data(
    bootstrap: CellBootstrap, *, queen_waggle_url_override: str | None = None
) -> str:
    """Render the NoCloud `user-data` cloud-config document for one Virtual Cell.

    Args:
        bootstrap: This Cell's freshly minted identity; `environment()` supplies every HIVEMIND_*
            value except the signing key, which is rewritten onto `HIVEMIND_CELL_SIGNING_KEY_FILE`
            (see the module docstring).
        queen_waggle_url_override: Replaces `HIVEMIND_QUEEN_WAGGLE_URL` when the network plan
            needs the Cell to dial a different, QEMU-reachable address (see
            `hivemind.hive.backends.qemu.network`'s own module docstring for when).

    Returns:
        A `#cloud-config` YAML document: three `write_files` entries (the signing key, the env
        file, the systemd unit) and a `runcmd` that enables and starts the unit.
    """
    env_lines = _render_env_lines(bootstrap, queen_waggle_url_override)
    parts = [
        "#cloud-config",
        "# Rendered by hivemind.hive.backends.qemu.cloud_init for one Virtual Cell (ADR-0027).",
        "write_files:",
        _write_file_entry(
            path=SIGNING_KEY_FILE_PATH,
            owner="hive:hive",
            permissions="0600",
            content=bootstrap.private_key_hex.get_secret_value(),
        ),
        _write_file_entry(
            path=ENV_FILE_PATH, owner="root:root", permissions="0600", content=env_lines
        ),
        _write_file_entry(
            path=SYSTEMD_UNIT_PATH,
            owner="root:root",
            permissions="0644",
            content=_render_systemd_unit(),
        ),
        "runcmd:",
        "  - systemctl daemon-reload",
        f"  - systemctl enable --now {SYSTEMD_UNIT_NAME}",
    ]
    return "\n".join(parts) + "\n"


def _render_env_lines(bootstrap: CellBootstrap, queen_waggle_url_override: str | None) -> str:
    """Build the env file's own KEY=VALUE lines, signing key routed through its own file."""
    env = dict(bootstrap.environment())
    if queen_waggle_url_override is not None:
        env[ENV_QUEEN_WAGGLE_URL_KEY] = queen_waggle_url_override
    # The image's own README documents HIVEMIND_CELL_SIGNING_KEY_FILE as one of two ways to supply
    # the key, and codingrules 15 prefers a mounted file to a bare environment variable; this
    # module always prefers it, so the raw key value is popped and replaced with the file pointer.
    env.pop("HIVEMIND_CELL_SIGNING_KEY", None)
    env["HIVEMIND_CELL_SIGNING_KEY_FILE"] = SIGNING_KEY_FILE_PATH
    # Deterministic order (dict insertion order is stable, and the keys above are inserted in a
    # fixed sequence) so this function's output can be snapshot-tested byte for byte.
    return "\n".join(f"{key}={value}" for key, value in env.items())


def _render_systemd_unit() -> str:
    """Build the hivemind-in-cell systemd unit: runs as `hive`, marks serial readiness first."""
    # ExecStartPre's leading "+" runs with full privileges regardless of the unit's own User=,
    # because /dev/ttyS0 (the VM's first serial port, matched to ProcessQemuRunner's own
    # `-serial file:...`) is not writable by the unprivileged `hive` user.
    return "\n".join(
        [
            "[Unit]",
            "Description=HiveMind in-Cell Warden entry point",
            "After=network-online.target",
            "Wants=network-online.target",
            "[Service]",
            "Type=simple",
            "User=hive",
            f"EnvironmentFile={ENV_FILE_PATH}",
            f'ExecStartPre=+/bin/sh -c \'printf "%s\\n" "{READINESS_MARKER}" > /dev/ttyS0\'',
            f"ExecStart={_HIVEMIND_IN_CELL_BIN}",
            "Restart=on-failure",
            "RestartSec=5",
            "[Install]",
            "WantedBy=multi-user.target",
        ]
    )


def _write_file_entry(*, path: str, owner: str, permissions: str, content: str) -> str:
    """Render one `write_files` list item, indenting `content` as a YAML literal block scalar."""
    indented = "\n".join(f"      {line}" if line else "" for line in content.splitlines())
    return (
        f"  - path: {path}\n"
        f"    owner: {owner}\n"
        f"    permissions: '{permissions}'\n"
        f"    content: |\n{indented}"
    )
