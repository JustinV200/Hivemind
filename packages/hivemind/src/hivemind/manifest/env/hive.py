"""Read the Hive's own `HIVEMIND_*` variables and fold them onto a loaded Hive Manifest.

Codingrules section 13: environment variables are read in exactly one place, the
`hivemind.manifest.env` package, are prefixed `HIVEMIND_`, and only override manifest values. This
module is that place for the Hive Stand's composition roots. ``read_env`` turns a raw environment
mapping into ``EnvOverrides``, a small typed value with one field per recognised variable; nothing
outside this package ever calls ``environ.get`` for a ``HIVEMIND_*`` name. ``apply_env`` then
folds those overrides onto an already-loaded ``HiveManifest``, replacing only the fields an
operator actually set. ``provider_api_key`` is the one place a provider's secret is read: never
from the manifest file itself (codingrules section 13 forbids an `api_key` field entirely), always
from an environment variable, held in a ``pydantic.SecretStr`` whose `repr` never shows the value;
the Entrance's Web Push VAPID key (``HIVEMIND_ENTRANCE_VAPID_PRIVATE_KEY``, ADR-0042) is read the
same way, by ``read_env``, beside its contact (``HIVEMIND_ENTRANCE_VAPID_SUBJECT``), and so is
every ``HIVEMIND_ENTRANCE_TUNNEL_<NAME>`` variable, the tokens the Entrance hands its tunnel client
(ADR-0041), collected under ``<NAME>``. ``read_build_image_sha256_override`` serves a third caller:
``scripts/build_cell_image.py`` has no Hive Manifest and no Cell of its own, but it still reads one
``HIVEMIND_*`` value (an operator-supplied override for the Ubuntu cloud image's expected digest).

Fits into the Hive:
    Layer 1 (foundational services; capacity as data), inside `hivemind.manifest.env`.
    ``read_env``/``apply_env`` are called by ``hivemind.manifest.loader.load_manifest`` when a
    caller passes an `environ` mapping; ``provider_api_key`` directly by whatever constructs a
    provider adapter; ``read_build_image_sha256_override`` by ``scripts/build_cell_image.py``.
    Calls into ``hivemind.manifest.env.common``, ``hivemind.manifest.errors`` and
    ``hivemind.manifest.schema`` only.

Key invariants:
    - No module outside this package reads `os.environ` for a `HIVEMIND_*` name (codingrules
      section 13); `read_env` takes the mapping as a parameter so it is never tempted to read the
      real environment itself, and so tests can pass a fake one.
    - `HIVEMIND_LLM_OFFLINE` accepts only "1", "true" or "yes" (case-insensitive) as true; any
      other value the variable is actually set to is a ManifestError (`common.read_offline_flag`).
    - `apply_env` never mutates `manifest`; every change is a fresh value from `model_copy`
      (codingrules section 8.5), and a manifest with no matching environment variables set comes
      back unchanged (the identical object, not merely an equal one).
    - `apply_env` re-validates through `HiveManifest.model_validate` after folding overrides in,
      raising `ManifestError` if the result is inconsistent (`model_copy` alone never re-runs a
      validator, so an override that recreates the offline/loopback or slot-completeness problem
      would otherwise slip through silently).

See Also:
    - .claude/codingrules.md section 13 for "environment variables are read in exactly one place"
      and "secrets are never in the manifest file".
    - hivemind.manifest.env.in_cell for the same rule at a Cell Warden's composition root.
    - hivemind.manifest.loader for load_manifest, the composition point that calls both functions.
    - hivemind.manifest.schema.llm for ProviderSpec.api_key_env, the per-provider override name.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, SecretStr, ValidationError

from hivemind.manifest.env.common import MODEL_CONFIG, read_offline_flag
from hivemind.manifest.errors import ManifestError
from hivemind.manifest.schema import HiveManifest, ProviderSpec

# The two [hive] env values; kept as a tuple so the validator's error message can list them.
_ENV_LITERALS = ("dev", "prod")
# Every variable with this prefix goes to the Entrance's tunnel child, renamed without it
# (ADR-0041): HIVEMIND_ENTRANCE_TUNNEL_TUNNEL_TOKEN reaches cloudflared as TUNNEL_TOKEN.
TUNNEL_VARIABLE_PREFIX = "HIVEMIND_ENTRANCE_TUNNEL_"

__all__ = [
    "TUNNEL_VARIABLE_PREFIX",
    "EnvOverrides",
    "apply_env",
    "provider_api_key",
    "read_build_image_sha256_override",
    "read_env",
]


class EnvOverrides(BaseModel):
    """Every `HIVEMIND_*` value `read_env` recognised; `None` means "not set, do not override"."""

    model_config = MODEL_CONFIG

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
        "environment the Entrance adds to its tunnel child's (ADR-0041), under the name the "
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
        llm_offline=read_offline_flag(environ),
        hive_stand_scratch_root=Path(scratch_root) if scratch_root is not None else None,
        log_level=environ.get("HIVEMIND_LOG_LEVEL"),
        env=_read_env_literal(environ),
        entrance_vapid_private_key=SecretStr(vapid_key) if vapid_key is not None else None,
        entrance_vapid_subject=environ.get("HIVEMIND_ENTRANCE_VAPID_SUBJECT"),
        entrance_tunnel_environ=_read_tunnel_environ(environ),
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
    manifest; dumping and re-validating forces them to run again over the new values. Only the
    fields the operator (or an override) set are dumped, so a validator that keys on "was this
    written" (`[royal_jelly] sandbox`) sees the same answer after the round trip as before it.
    """
    try:
        revalidated = HiveManifest.model_validate(
            manifest.model_dump(mode="json", exclude_unset=True)
        )
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
