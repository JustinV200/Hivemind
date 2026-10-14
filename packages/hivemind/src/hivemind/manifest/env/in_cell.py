"""Read the `HIVEMIND_*` variables a Cell's own Warden starts with: `InCellEnv`.

The in-Cell Warden entry point (``hivemind.cli.in_cell``) has no Hive Manifest to load inside its
Virtual Cell image, so every value it needs -- the Queen's Waggle URL, this Cell's own id and
signing key, the Queen's verify key -- is read here (roadmap step 5.5) rather than opening a
second `HIVEMIND_*` reading site (codingrules section 13). Roadmap step 11.6a adds the variables
the Pollen Packet gives the Warden it starts on a colonized device (``HIVEMIND_CELL_LEASE=borrowed``
and the ids, level, state directory, pin and manifest it needs), step 11.10 the directories the
operator named for that Warden to watch (``HIVEMIND_WATCH_ROOTS``, a JSON array like the provider
and slot tables), and the Night Veil link the Hive's offline limit (``HIVEMIND_MAX_OFFLINE_S``).

Fits into the Hive:
    Layer 1 (foundational services; capacity as data), inside `hivemind.manifest.env`. Called by
    ``hivemind.cli.in_cell``, a Cell Warden's own composition root. Calls into
    ``hivemind.manifest.env.common``, ``hivemind.manifest.errors`` and pydantic only.

Key invariants:
    - `read_in_cell_env` never raises for a missing variable and never parses key material into
      bytes: it only extracts what `environ` holds; `hivemind.cli.in_cell` decides which fields are
      required and does the hex/file parsing itself, so this module's "read the environment,
      nothing else" scope never grows a second kind of side effect.
    - `InCellEnv.environ` carries the *whole* mapping `read_in_cell_env` was given, not just the
      names this module otherwise extracts: `HIVEMIND_PROVIDERS`' own `api_key_env` entries name
      variables (e.g. `HIVEMIND_ANTHROPIC_API_KEY`) this module cannot enumerate ahead of parsing
      that JSON, and `hivemind.cli.in_cell.config` (which does parse it) has no second `os.environ`
      of its own to read one from -- codingrules section 13 still holds, since this is the same
      single read `read_in_cell_env` already performed, only retained rather than discarded.

See Also:
    - .claude/codingrules.md section 13 for "environment variables are read in exactly one place".
    - hivemind.manifest.env.hive for the same rule at the Hive Stand's composition roots.
    - hivemind.cli.in_cell.config for what is done with each value.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Annotated

from pydantic import BaseModel, Field, SecretStr, TypeAdapter, ValidationError

from hivemind.manifest.env.common import MODEL_CONFIG, read_offline_flag
from hivemind.manifest.errors import ManifestError

# What HIVEMIND_MAX_OFFLINE_S must be: a limit that never passes (or passes at once) would cluster
# nothing, or everything, so it is a positive, finite number of seconds.
_SECONDS: TypeAdapter[float] = TypeAdapter(Annotated[float, Field(gt=0, allow_inf_nan=False)])

__all__ = ["InCellEnv", "read_in_cell_env"]


class InCellEnv(BaseModel):
    """Every `HIVEMIND_*` value the in-Cell Warden entry point (`hivemind.cli.in_cell`) reads.

    Unlike `EnvOverrides`, none of these override a `HiveManifest` section -- a Virtual Cell image
    carries no manifest of its own. `None` means the variable is not set; this module never
    decides which of these are required (`read_env`'s own rule: extraction only), so
    `hivemind.cli.in_cell` raises its own error for whichever one it cannot start without.
    """

    model_config = MODEL_CONFIG

    queen_waggle_url: str | None = Field(
        default=None, description="HIVEMIND_QUEEN_WAGGLE_URL: the URL this Cell dials out to."
    )
    cell_id: str | None = Field(
        default=None, description="HIVEMIND_CELL_ID: the id the Queen minted for this Cell."
    )
    hive_id: str | None = Field(
        default=None,
        description="HIVEMIND_HIVE_ID: the Queen's own bee address (sender/"
        "recipient on the wire, waggle.ids.IdKind.HIVE), not a NodeId.",
    )
    queen_node_id: str | None = Field(
        default=None,
        description="HIVEMIND_QUEEN_NODE_ID: the node id the Queen signs its own frames as, so "
        "this Cell's Verifier knows whose signature to check (waggle.signing.Ed25519Verifier is "
        "keyed by node_id, not by hive_id).",
    )
    signing_key_hex: SecretStr | None = Field(
        default=None,
        description="HIVEMIND_CELL_SIGNING_KEY: this Cell's own Ed25519 private key, hex-encoded "
        "(the Queen provisions it into the container at create time).",
    )
    signing_key_file: Path | None = Field(
        default=None,
        description="HIVEMIND_CELL_SIGNING_KEY_FILE: a file holding the same hex text, for a "
        "mounted secret instead of a bare environment variable (codingrules section 15).",
    )
    queen_verify_key_hex: str | None = Field(
        default=None,
        description="HIVEMIND_QUEEN_VERIFY_KEY: the Queen's own Ed25519 public key, hex-encoded, "
        "so this Cell can verify frames the Queen sends.",
    )
    queen_verify_key_file: Path | None = Field(
        default=None,
        description="HIVEMIND_QUEEN_VERIFY_KEY_FILE: a file holding the same hex text.",
    )
    socks_proxy_url: str | None = Field(
        default=None,
        description="HIVEMIND_SOCKS_PROXY_URL: the loopback SOCKS proxy (socks5h/socks4a) every "
        "Waggle dial goes through: a Night Veil Cell's Tor SOCKS port (roadmap step 10.3a).",
    )
    max_offline_s: float | None = Field(
        default=None,
        description="HIVEMIND_MAX_OFFLINE_S: the Hive's `[supervision] max_offline_s`, in seconds.",
    )
    comb_shield: str | None = Field(
        default=None,
        description="HIVEMIND_COMB_SHIELD: the tier the Queen provisioned this Cell at (roadmap "
        "step 10.3a), validated by hivemind.cli.in_cell.config; unset reads as MEADOW.",
    )
    reservation_json: str | None = Field(
        default=None,
        description="HIVEMIND_RESERVATION: what the backend reserved for this Cell, as JSON "
        "(hivemind.hive.models.CellReservation), the capacity this Cell reports; parsed by "
        "hivemind.cli.in_cell.config, never here (extraction only).",
    )
    scratch_root: Path | None = Field(
        default=None,
        description="HIVEMIND_SCRATCH_ROOT: where this Cell's Warden creates each lease's own "
        "scratch directory; unset means hivemind.cli.in_cell.config.DEFAULT_SCRATCH_ROOT, the "
        "path images/base-ubuntu creates. Set by a test or an in-process Cell that cannot write "
        "there (Linux CI cannot create /var/lib/hivemind; Windows quietly could, which hid it).",
    )
    providers_json: str | None = Field(
        default=None,
        description="HIVEMIND_PROVIDERS: this Hive's own [llm.providers] table, as a JSON array "
        "(hivemind.hive.backends.provider_table.render_providers_json); parsed and validated by "
        "hivemind.cli.in_cell.config, never here (extraction only, per this module's own rule).",
    )
    slots_json: str | None = Field(
        default=None,
        description="HIVEMIND_SLOTS: this Hive's own [llm.slots] table, as a JSON array "
        "(hivemind.hive.backends.provider_table.render_slots_json); parsed the same way.",
    )
    llm_offline: bool | None = Field(
        default=None,
        description="HIVEMIND_LLM_OFFLINE: the same variable name (and meaning) "
        "EnvOverrides.llm_offline reads for the Hive Stand, carried through so this Cell's own "
        "ProviderRegistry enforces the identical [llm] offline policy.",
    )
    environ: Mapping[str, str] = Field(
        default_factory=dict,
        description="The whole environment mapping read_in_cell_env was given; see this module's "
        "own Key invariants for why this one field is a passthrough rather than an extraction.",
    )
    log_level: str | None = Field(
        default=None,
        description="HIVEMIND_LOG_LEVEL: read by hivemind.common.logging's setup, the same "
        "variable name (and meaning) EnvOverrides.log_level reads for the Hive Stand.",
    )
    cell_lease: str | None = Field(
        default=None,
        description="HIVEMIND_CELL_LEASE: `borrowed` for the Warden of a colonized Swarm device "
        "(roadmap step 11.6a), which borrows the machine it runs on; unset means `owned`.",
    )
    warden_id: str | None = Field(
        default=None, description="HIVEMIND_WARDEN_ID: borrowed mode keeps the Warden's id."
    )
    node_id: str | None = Field(
        default=None, description="HIVEMIND_NODE_ID: the device's node id, whose key signs."
    )
    access_level: str | None = Field(
        default=None, description="HIVEMIND_ACCESS_LEVEL: the level the Queen granted the device."
    )
    state_dir: Path | None = Field(
        default=None,
        description="HIVEMIND_STATE_DIR: where a borrowed Warden keeps its SQLite stores and "
        "outbox, so a Handoff survives its restart.",
    )
    tls_pin: str | None = Field(
        default=None, description="HIVEMIND_TLS_PIN: the swarm listener's certificate pin."
    )
    device_manifest: Path | None = Field(
        default=None, description="HIVEMIND_DEVICE_MANIFEST: the device's local `[llm]` manifest."
    )
    watch_roots_json: str | None = Field(
        default=None,
        description="HIVEMIND_WATCH_ROOTS: the directories the operator named for this Warden to "
        "watch, as a JSON array of paths; unset means it watches none.",
    )


def read_in_cell_env(environ: Mapping[str, str]) -> InCellEnv:
    """Read every `HIVEMIND_*` variable the in-Cell Warden entry point needs.

    Args:
        environ: A raw environment mapping, e.g. `os.environ` at `hivemind.cli.in_cell`'s own
            composition root; this function never reads `os.environ` itself (codingrules 13).

    Returns:
        An InCellEnv with one field set per variable actually present in `environ`.

    Raises:
        ManifestError: `HIVEMIND_LLM_OFFLINE` is set to a value `read_offline_flag` does not
            recognise (the same rule `read_env` applies for the Hive Stand), or
            `HIVEMIND_MAX_OFFLINE_S` is not a positive, finite number of seconds; never raised for
            a variable that is simply absent.
    """
    signing_key = environ.get("HIVEMIND_CELL_SIGNING_KEY")
    signing_key_file = environ.get("HIVEMIND_CELL_SIGNING_KEY_FILE")
    verify_key_file = environ.get("HIVEMIND_QUEEN_VERIFY_KEY_FILE")
    scratch_root = environ.get("HIVEMIND_SCRATCH_ROOT")
    state_dir = environ.get("HIVEMIND_STATE_DIR")
    device_manifest = environ.get("HIVEMIND_DEVICE_MANIFEST")
    return InCellEnv(
        queen_waggle_url=environ.get("HIVEMIND_QUEEN_WAGGLE_URL"),
        cell_id=environ.get("HIVEMIND_CELL_ID"),
        hive_id=environ.get("HIVEMIND_HIVE_ID"),
        queen_node_id=environ.get("HIVEMIND_QUEEN_NODE_ID"),
        signing_key_hex=SecretStr(signing_key) if signing_key is not None else None,
        signing_key_file=Path(signing_key_file) if signing_key_file is not None else None,
        queen_verify_key_hex=environ.get("HIVEMIND_QUEEN_VERIFY_KEY"),
        queen_verify_key_file=Path(verify_key_file) if verify_key_file is not None else None,
        socks_proxy_url=environ.get("HIVEMIND_SOCKS_PROXY_URL"),
        max_offline_s=_read_offline_limit(environ),
        comb_shield=environ.get("HIVEMIND_COMB_SHIELD"),
        reservation_json=environ.get("HIVEMIND_RESERVATION"),
        scratch_root=Path(scratch_root) if scratch_root is not None else None,
        providers_json=environ.get("HIVEMIND_PROVIDERS"),
        slots_json=environ.get("HIVEMIND_SLOTS"),
        llm_offline=read_offline_flag(environ),
        environ=environ,
        log_level=environ.get("HIVEMIND_LOG_LEVEL"),
        cell_lease=environ.get("HIVEMIND_CELL_LEASE"),
        warden_id=environ.get("HIVEMIND_WARDEN_ID"),
        node_id=environ.get("HIVEMIND_NODE_ID"),
        access_level=environ.get("HIVEMIND_ACCESS_LEVEL"),
        state_dir=Path(state_dir) if state_dir is not None else None,
        tls_pin=environ.get("HIVEMIND_TLS_PIN"),
        device_manifest=Path(device_manifest) if device_manifest is not None else None,
        watch_roots_json=environ.get("HIVEMIND_WATCH_ROOTS"),
    )


def _read_offline_limit(environ: Mapping[str, str]) -> float | None:
    """Parse HIVEMIND_MAX_OFFLINE_S, or return None when it is not set."""
    if (raw := environ.get("HIVEMIND_MAX_OFFLINE_S")) is None:
        return None
    try:
        return _SECONDS.validate_python(raw)
    except ValidationError as exc:
        raise ManifestError(f"HIVEMIND_MAX_OFFLINE_S={raw!r} is not a positive number.") from exc
