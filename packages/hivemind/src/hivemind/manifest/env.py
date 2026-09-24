"""Read every `HIVEMIND_*` environment variable in exactly one place, and apply it to a manifest.

Codingrules section 13: "Environment variables are read in exactly one place (`manifest/env.py`),
are prefixed `HIVEMIND_`, and only override manifest values." This module is that one place.
``read_env`` turns a raw environment mapping into ``EnvOverrides``, a small typed value with one
field per recognised variable; nothing outside this function ever calls ``environ.get`` for a
``HIVEMIND_*`` name. ``apply_env`` then folds those overrides onto an already-loaded
``HiveManifest``, replacing only the fields an operator actually set. ``provider_api_key`` is the
one place a provider's secret is read: never from the manifest file itself (codingrules section 13
forbids an `api_key` field entirely), always from an environment variable, held in a
``pydantic.SecretStr`` whose `repr` never shows the value; the Entrance's Web Push VAPID key
(``HIVEMIND_ENTRANCE_VAPID_PRIVATE_KEY``, ADR-0034) is read the same way, by ``read_env``, beside
its contact (``HIVEMIND_ENTRANCE_VAPID_SUBJECT``), and so is every
``HIVEMIND_ENTRANCE_TUNNEL_<NAME>`` variable, the tokens the Entrance hands its tunnel client
(ADR-0033), collected under ``<NAME>``. ``read_in_cell_env`` is the same rule
for a different composition root (roadmap step 5.5): the in-Cell Warden entry point
(``hivemind.cli.in_cell``) has no Hive Manifest to load inside its Virtual Cell image, so every
value it needs -- the Queen's Waggle URL, this Cell's own id and signing key, the Queen's verify
key -- is read here too, into ``InCellEnv``, rather than opening a second `HIVEMIND_*` reading
site.

Fits into the Hive:
    Layer 1 (foundational services; capacity as data). ``read_env``/``apply_env`` are called by
    ``hivemind.manifest.loader.load_manifest`` when a caller passes an `environ` mapping;
    ``provider_api_key`` directly by whatever constructs a provider adapter; ``read_in_cell_env``
    by ``hivemind.cli.in_cell``, the in-Cell Warden's own composition root. ``read_build_image_
    sha256_override`` is the same rule for a third caller (roadmap step 5.11, this branch):
    ``scripts/build_cell_image.py`` is a dev/build-time script with no Hive Manifest and no Cell of
    its own, but it still reads one ``HIVEMIND_*`` value (an operator-supplied override for the
    Ubuntu cloud image's expected digest), so that read lives here too rather than opening a fourth
    site. Calls into ``hivemind.manifest.errors`` and ``hivemind.manifest.schema`` only.

Key invariants:
    - No module outside this one reads `os.environ` for a `HIVEMIND_*` name (codingrules section
      13); `read_env` takes the mapping as a parameter so it is never tempted to read the real
      environment itself, and so tests can pass a fake one.
    - `HIVEMIND_LLM_OFFLINE` accepts only "1", "true" or "yes" (case-insensitive) as true; any
      other value the variable is actually set to is a ManifestError, not a silently-ignored
      override -- an operator who sets it expects it to take effect, and "false"/"0" would invite
      the mistaken belief that unsetting the flag this way turns offline mode off.
    - `apply_env` never mutates `manifest`; every change is a fresh value from `model_copy`
      (codingrules section 8.5), and a manifest with no matching environment variables set comes
      back unchanged (the identical object, not merely an equal one).
    - `apply_env` re-validates through `HiveManifest.model_validate` after folding overrides in,
      raising `ManifestError` if the result is inconsistent (`model_copy` alone never re-runs a
      validator, so an override that recreates the offline/loopback or slot-completeness problem
      would otherwise slip through silently).
    - `read_in_cell_env` never raises for a missing variable and never parses key material into
      bytes: it only extracts what `environ` holds, exactly like `read_env`; `hivemind.cli.in_cell`
      decides which fields are required and does the hex/file parsing itself, so this module's own
      "read the environment, nothing else" scope never grows a second kind of side effect.
    - `InCellEnv.environ` carries the *whole* mapping `read_in_cell_env` was given, not just the
      names this module otherwise extracts: `HIVEMIND_PROVIDERS`' own `api_key_env` entries name
      variables (e.g. `HIVEMIND_ANTHROPIC_API_KEY`) this module cannot enumerate ahead of parsing
      that JSON, and `hivemind.cli.in_cell.config` (which does parse it) has no second `os.environ`
      of its own to read one from -- codingrules section 13 still holds, since this is the same
      single read `read_in_cell_env` already performed, only retained rather than discarded.

See Also:
    - .claude/codingrules.md section 13 for "environment variables are read in exactly one place".
    - .claude/codingrules.md section 13 for "secrets are never in the manifest file".
    - hivemind.manifest.loader for load_manifest, the composition point that calls both functions.
    - hivemind.manifest.schema.llm for ProviderSpec.api_key_env, the per-provider override name.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError

from hivemind.manifest.errors import ManifestError
from hivemind.manifest.schema import HiveManifest, ProviderSpec

# Every value HIVEMIND_LLM_OFFLINE may take to mean true; anything else the variable is actually
# set to is a typo worth surfacing rather than silently treating as false (see module docstring).
_TRUE_BOOL_LITERALS = frozenset({"1", "true", "yes"})
# The two [hive] env values; kept as a tuple so the validator's error message can list them.
_ENV_LITERALS = ("dev", "prod")
# Every variable with this prefix goes to the Entrance's tunnel child, renamed without it
# (ADR-0033): HIVEMIND_ENTRANCE_TUNNEL_TUNNEL_TOKEN reaches cloudflared as TUNNEL_TOKEN.
TUNNEL_VARIABLE_PREFIX = "HIVEMIND_ENTRANCE_TUNNEL_"

__all__ = [
    "TUNNEL_VARIABLE_PREFIX",
    "EnvOverrides",
    "InCellEnv",
    "apply_env",
    "provider_api_key",
    "read_build_image_sha256_override",
    "read_env",
    "read_in_cell_env",
]

# A frozen, extras-forbidding config matching every other boundary value (codingrules section 8.5).
_MODEL_CONFIG = ConfigDict(frozen=True, extra="forbid")


class EnvOverrides(BaseModel):
    """Every `HIVEMIND_*` value `read_env` recognised; `None` means "not set, do not override"."""

    model_config = _MODEL_CONFIG

    db: Path | None = Field(default=None, description="HIVEMIND_DB: overrides [hive] db.")
    llm_offline: bool | None = Field(
        default=None, description="HIVEMIND_LLM_OFFLINE: overrides [llm] offline."
    )
    hive_stand_scratch_root: Path | None = Field(
        default=None,
        description="HIVEMIND_HIVE_STAND_SCRATCH_ROOT: overrides [hive_stand] scratch_root.",
    )
    log_level: str | None = Field(
        default=None,
        description="HIVEMIND_LOG_LEVEL: read by hivemind.common.logging's setup; no manifest "
        "field corresponds to it, so apply_env does not touch the manifest for this one.",
    )
    env: Literal["dev", "prod"] | None = Field(
        default=None, description="HIVEMIND_ENV: overrides [hive] env."
    )
    entrance_vapid_private_key: SecretStr | None = Field(
        default=None,
        description="HIVEMIND_ENTRANCE_VAPID_PRIVATE_KEY: the Entrance's Web Push VAPID private "
        "key, base64url of the raw 32-byte P-256 scalar; used instead of the one minted into the "
        "secret store (entrance.vapid). A secret, so SecretStr; no manifest field corresponds to "
        "it, so apply_env does not touch the manifest for this one.",
    )
    entrance_vapid_subject: str | None = Field(
        default=None,
        description="HIVEMIND_ENTRANCE_VAPID_SUBJECT: the VAPID contact push services may use, a "
        "mailto: or https: URI; no manifest field corresponds to it either.",
    )
    entrance_tunnel_environ: Mapping[str, SecretStr] = Field(
        default_factory=dict,
        description="Every HIVEMIND_ENTRANCE_TUNNEL_<NAME> variable, keyed by <NAME>: the "
        "environment the Entrance adds to its tunnel child's (ADR-0033), under the name the "
        "tunnel client itself reads, since no shell expands variables in its argv. Secrets, so "
        "SecretStr; handed to the child only, never to a manifest field.",
    )


def read_env(environ: Mapping[str, str]) -> EnvOverrides:
    """Read every recognised `HIVEMIND_*` variable out of `environ`.

    Args:
        environ: A raw environment mapping, e.g. `os.environ` at the caller's own composition
            root; this function never reads `os.environ` itself (codingrules section 13).

    Returns:
        An EnvOverrides with one field set per variable actually present in `environ`; a variable
        that is not present leaves its field `None`.

    Raises:
        ManifestError: `HIVEMIND_LLM_OFFLINE` or `HIVEMIND_ENV` is set to a value this module does
            not recognise, or a variable is named exactly `HIVEMIND_ENTRANCE_TUNNEL_` (no name
            left to hand the tunnel child).
    """
    db = environ.get("HIVEMIND_DB")
    scratch_root = environ.get("HIVEMIND_HIVE_STAND_SCRATCH_ROOT")
    # Held in a SecretStr from the moment it is read, so no repr of the overrides can show it.
    vapid_key = environ.get("HIVEMIND_ENTRANCE_VAPID_PRIVATE_KEY")
    return EnvOverrides(
        db=Path(db) if db is not None else None,
        llm_offline=_read_offline_flag(environ),
        hive_stand_scratch_root=Path(scratch_root) if scratch_root is not None else None,
        log_level=environ.get("HIVEMIND_LOG_LEVEL"),
        env=_read_env_literal(environ),
        entrance_vapid_private_key=SecretStr(vapid_key) if vapid_key is not None else None,
        entrance_vapid_subject=environ.get("HIVEMIND_ENTRANCE_VAPID_SUBJECT"),
        entrance_tunnel_environ=_read_tunnel_environ(environ),
    )


class InCellEnv(BaseModel):
    """Every `HIVEMIND_*` value the in-Cell Warden entry point (`hivemind.cli.in_cell`) reads.

    Unlike `EnvOverrides`, none of these override a `HiveManifest` section -- a Virtual Cell image
    carries no manifest of its own. `None` means the variable is not set; this module never
    decides which of these are required (`read_env`'s own rule: extraction only), so
    `hivemind.cli.in_cell` raises its own error for whichever one it cannot start without.
    """

    model_config = _MODEL_CONFIG

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
    comb_shield: str | None = Field(
        default=None,
        description="HIVEMIND_COMB_SHIELD: the tier the Queen provisioned this Cell at (roadmap "
        "step 10.3a), validated by hivemind.cli.in_cell.config; unset reads as MEADOW.",
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


def read_in_cell_env(environ: Mapping[str, str]) -> InCellEnv:
    """Read every `HIVEMIND_*` variable the in-Cell Warden entry point needs.

    Args:
        environ: A raw environment mapping, e.g. `os.environ` at `hivemind.cli.in_cell`'s own
            composition root; this function never reads `os.environ` itself (codingrules 13).

    Returns:
        An InCellEnv with one field set per variable actually present in `environ`.

    Raises:
        ManifestError: `HIVEMIND_LLM_OFFLINE` is set to a value `_read_offline_flag` does not
            recognise (the same rule `read_env` applies for the Hive Stand); never raised for a
            variable that is simply absent.
    """
    signing_key = environ.get("HIVEMIND_CELL_SIGNING_KEY")
    signing_key_file = environ.get("HIVEMIND_CELL_SIGNING_KEY_FILE")
    verify_key_file = environ.get("HIVEMIND_QUEEN_VERIFY_KEY_FILE")
    scratch_root = environ.get("HIVEMIND_SCRATCH_ROOT")
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
        comb_shield=environ.get("HIVEMIND_COMB_SHIELD"),
        scratch_root=Path(scratch_root) if scratch_root is not None else None,
        providers_json=environ.get("HIVEMIND_PROVIDERS"),
        slots_json=environ.get("HIVEMIND_SLOTS"),
        llm_offline=_read_offline_flag(environ),
        environ=environ,
        log_level=environ.get("HIVEMIND_LOG_LEVEL"),
    )


def apply_env(manifest: HiveManifest, overrides: EnvOverrides) -> HiveManifest:
    """Return `manifest` with every set field of `overrides` folded in.

    Args:
        manifest: The manifest to override. Never mutated.
        overrides: The result of `read_env`; a `None` field leaves the matching manifest value
            untouched.

    Returns:
        `manifest` unchanged if no override applies, otherwise a fresh, fully re-validated
        `HiveManifest` with the matching sections replaced.

    Raises:
        ManifestError: The overridden values, taken together, fail `HiveManifest`'s own
            validation (e.g. `HIVEMIND_LLM_OFFLINE=true` against a manifest whose only provider
            is hosted). `model_copy(update=...)` does not re-run validators on its own
            (codingrules section 8.5's "frozen-safe update"), so this function re-validates
            explicitly rather than silently handing back an inconsistent manifest.
    """
    section_updates = _section_updates(manifest, overrides)
    if not section_updates:
        return manifest  # No matching variable was set; return the identical object.
    updated = manifest.model_copy(update=section_updates)
    return _revalidated(updated, source_path=manifest.source_path)


def _section_updates(manifest: HiveManifest, overrides: EnvOverrides) -> dict[str, object]:
    """Build the top-level `HiveManifest` field updates `apply_env` needs, one per set override."""
    hive_updates: dict[str, object] = {}
    if overrides.db is not None:
        hive_updates["db"] = overrides.db
    if overrides.env is not None:
        hive_updates["env"] = overrides.env

    updates: dict[str, object] = {}
    if hive_updates:
        updates["hive"] = manifest.hive.model_copy(update=hive_updates)
    if overrides.llm_offline is not None:
        updates["llm"] = manifest.llm.model_copy(update={"offline": overrides.llm_offline})
    if overrides.hive_stand_scratch_root is not None:
        updates["hive_stand"] = manifest.hive_stand.model_copy(
            update={"scratch_root": overrides.hive_stand_scratch_root}
        )
    # log_level, the two entrance_vapid_* values and entrance_tunnel_environ have no manifest
    # field (see EnvOverrides' field descriptions): the Entrance's composition root reads them off
    # the overrides instead.
    return updates


def _revalidated(manifest: HiveManifest, *, source_path: Path | None) -> HiveManifest:
    """Force every HiveManifest validator to re-run over `manifest`, restoring `source_path`.

    `model_copy(update=...)` never re-runs validators, so the offline/loopback and
    slot-completeness checks would otherwise silently stop applying to an env-overridden
    manifest; dumping and re-validating forces them to run again over the new values.
    """
    try:
        revalidated = HiveManifest.model_validate(manifest.model_dump(mode="json"))
    except ValidationError as exc:
        raise ManifestError(
            f"Applying environment overrides produced an invalid Hive Manifest: {exc}"
        ) from exc
    # model_dump excludes source_path (HiveManifest.source_path is Field(exclude=True)); carry
    # the original file's path forward rather than losing it to the round trip.
    return revalidated.model_copy(update={"source_path": source_path})


def provider_api_key(name: str, spec: ProviderSpec, environ: Mapping[str, str]) -> SecretStr | None:
    """Read one provider's API key from the environment, never from the manifest file.

    Args:
        name: The provider's `[llm.providers.<name>]` key.
        spec: That provider's spec; only `api_key_env` is read.
        environ: A raw environment mapping (never read directly from `os.environ` by this module).

    Returns:
        A `SecretStr` (its `repr` hides the value) when the variable is set, else `None`. `None`
        is a legitimate result for a local provider that needs no key.
    """
    # spec.api_key_env, when set, always wins; otherwise the derived HIVEMIND_<NAME>_API_KEY name
    # is exactly what codingrules section 13 documents as the default.
    var_name = spec.api_key_env or f"HIVEMIND_{name.upper()}_API_KEY"
    raw = environ.get(var_name)
    return SecretStr(raw) if raw is not None else None


def read_build_image_sha256_override(environ: Mapping[str, str]) -> str | None:
    """Read HIVEMIND_QEMU_BASE_IMAGE_SHA256, the digest override `scripts/build_cell_image.py` uses.

    That script refuses to build past its own bundled `UBUNTU_CLOUD_IMAGE_SHA256` while it still
    holds the documented placeholder; this lets an operator who already knows the real digest for
    the release they are pinning (e.g. read fresh from Ubuntu's own SHA256SUMS file) pass it
    without editing the script (`--sha256` on the CLI is the other way; that script prefers the
    flag when both are given).

    Args:
        environ: A raw environment mapping, e.g. `os.environ` at the script's own composition
            root; this function never reads `os.environ` itself (codingrules section 13).

    Returns:
        The raw digest string if the variable is set, else None. Not validated as hex or length
        here (extraction only, module docstring): `scripts/build_cell_image.py` compares it
        against the real download's own computed digest, which already rejects a wrong value.
    """
    return environ.get("HIVEMIND_QEMU_BASE_IMAGE_SHA256")


def _read_offline_flag(environ: Mapping[str, str]) -> bool | None:
    """Parse HIVEMIND_LLM_OFFLINE, or return None when it is not set."""
    raw = environ.get("HIVEMIND_LLM_OFFLINE")
    if raw is None:
        return None
    if raw.strip().lower() in _TRUE_BOOL_LITERALS:
        return True
    raise ManifestError(
        f"HIVEMIND_LLM_OFFLINE={raw!r} is not a recognised boolean "
        f"(expected one of {sorted(_TRUE_BOOL_LITERALS)}, case-insensitive)."
    )


def _read_tunnel_environ(environ: Mapping[str, str]) -> dict[str, SecretStr]:
    """Collect every HIVEMIND_ENTRANCE_TUNNEL_<NAME> variable as <NAME>, its value held secret."""
    found: dict[str, SecretStr] = {}
    # Every variable is looked at, since the names after the prefix are the operator's choice.
    for name, value in environ.items():
        if not name.startswith(TUNNEL_VARIABLE_PREFIX):
            continue
        child_name = name.removeprefix(TUNNEL_VARIABLE_PREFIX)
        # The bare prefix names nothing the child could read; a typo worth surfacing. The
        # message names the variable, never its value (a token).
        if not child_name:
            raise ManifestError(
                f"{TUNNEL_VARIABLE_PREFIX} is set without a name after the prefix; use "
                f"{TUNNEL_VARIABLE_PREFIX}<NAME> to hand the tunnel client <NAME>."
            )
        found[child_name] = SecretStr(value)
    return found


def _read_env_literal(environ: Mapping[str, str]) -> Literal["dev", "prod"] | None:
    """Parse HIVEMIND_ENV, or return None when it is not set."""
    raw = environ.get("HIVEMIND_ENV")
    if raw is None:
        return None
    lowered = raw.strip().lower()
    if lowered in _ENV_LITERALS:
        # mypy needs the narrowed literal spelled out; the `in` check above already proved it.
        return "dev" if lowered == "dev" else "prod"
    raise ManifestError(f"HIVEMIND_ENV={raw!r} is not one of {_ENV_LITERALS}.")
