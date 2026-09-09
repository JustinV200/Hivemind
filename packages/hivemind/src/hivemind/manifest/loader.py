"""Load a Hive Manifest TOML file into a validated HiveManifest, applying env overrides.

``load_manifest`` is the one function that turns a file on disk into a usable, typed
``HiveManifest``: read the bytes, parse them as TOML (`tomllib`, the standard library's own
parser), validate the result into ``HiveManifest`` (codingrules section 9: "pydantic models are
the only thing that reads JSON/TOML" -- the raw `dict` `tomllib` produces never leaves this
function), stamp `source_path` so `HiveManifest.resolve_path` knows the manifest's own directory,
and, when a caller supplies an environment mapping, fold in `hivemind.manifest.env`'s overrides.
Every way this can fail -- an unreadable file, invalid TOML syntax, or a value that fails
`HiveManifest`'s own validators -- becomes one `ManifestError` naming the file and, for a
validation failure, the dotted field location pydantic reported, so a caller never has to parse a
raw `pydantic.ValidationError` to find out what to fix.

Fits into the Hive:
    Layer 1 (foundational services; capacity as data). Called by the composition root
    (`cli/stores.py`, a later roadmap step) and by tests that load the shipped example manifests.
    Calls into `hivemind.manifest.env`, `hivemind.manifest.errors` and `hivemind.manifest.schema`
    only.

Key invariants:
    - `load_manifest` never returns a partially-built manifest: it either returns a fully
      validated `HiveManifest` or raises `ManifestError`.
    - The raw `dict` `tomllib.load` produces is never returned, stored, or handed to anything but
      `HiveManifest.model_validate` (codingrules section 9).
    - `environ=None` (the default) applies no environment overrides at all, so a caller that wants
      a pure, deterministic read of the file alone gets exactly that.
    - When a document has more than one problem and at least one is a section's own
      `default_factory` failing (an omitted `[llm]` or `[forage]` whose content requirement an
      empty table cannot satisfy), pydantic reports only that failure, not a sibling "field
      required" error elsewhere in the document; `_describe_validation_error` still reports every
      error pydantic actually returned, faithfully, it just cannot surface one pydantic itself
      dropped.

See Also:
    - .claude/codingrules.md section 9 for "pydantic models are the only thing that reads TOML".
    - .claude/codingrules.md section 13 for the Hive Manifest and environment-override rules.
    - hivemind.manifest.env for read_env and apply_env, the two functions this module composes.
    - hivemind.manifest.schema for HiveManifest, the model this module validates into.
"""

from __future__ import annotations

import tomllib
from collections.abc import Mapping
from pathlib import Path

from pydantic import ValidationError

from hivemind.manifest.env import apply_env, read_env
from hivemind.manifest.errors import ManifestError
from hivemind.manifest.schema import HiveManifest

__all__ = ["load_manifest"]


def load_manifest(path: Path, environ: Mapping[str, str] | None = None) -> HiveManifest:
    """Load, validate, and (optionally) environment-override a Hive Manifest from `path`.

    Args:
        path: The manifest TOML file to load.
        environ: A raw environment mapping to read `HIVEMIND_*` overrides from
            (`hivemind.manifest.env.read_env`); `None` (the default) applies no overrides at all.
            Never `os.environ` implicitly -- codingrules section 13 requires the caller to pass it.

    Returns:
        The validated `HiveManifest`, with `source_path` set to `path` and any environment
        overrides already applied.

    Raises:
        ManifestError: `path` could not be read, its content is not valid TOML, its content fails
            `HiveManifest`'s own validation, or an environment override was set to a value
            `hivemind.manifest.env.read_env` does not recognise.
    """
    try:
        with path.open("rb") as handle:
            # tomllib wants a binary handle; it decodes the document as UTF-8 itself.
            raw = tomllib.load(handle)
    except OSError as exc:
        raise ManifestError(f"Could not read Hive Manifest {path}: {exc}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise ManifestError(f"Hive Manifest {path} is not valid TOML: {exc}") from exc

    try:
        manifest = HiveManifest.model_validate(raw)
    except ValidationError as exc:
        raise ManifestError(_describe_validation_error(path, exc)) from exc

    # source_path must be set before resolve_path (and anything that calls it, like a later
    # HIVEMIND_HIVE_STAND_SCRATCH_ROOT override that a caller resolves) has anything to work with.
    manifest = manifest.model_copy(update={"source_path": path})
    if environ is not None:
        manifest = apply_env(manifest, read_env(environ))
    return manifest


def _describe_validation_error(path: Path, exc: ValidationError) -> str:
    """Turn a pydantic ValidationError into one message naming the file and every dotted field.

    Args:
        path: The manifest file that failed to validate, for the message's context.
        exc: The ValidationError HiveManifest.model_validate raised.

    Returns:
        One sentence per error, semicolon-joined, each naming the dotted location
        (`llm.slots.queen.provider` style) pydantic reported and its message.
    """
    parts = []
    for error in exc.errors():
        # A model-level validator (HiveManifest's own @model_validator) reports an empty loc; the
        # placeholder keeps the message readable instead of starting with a bare colon.
        location = ".".join(str(segment) for segment in error["loc"]) or "<manifest root>"
        parts.append(f"{location}: {error['msg']}")
    return f"Hive Manifest {path} failed validation: {'; '.join(parts)}"
