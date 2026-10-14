"""Read every `HIVEMIND_*` environment variable in exactly one place, and apply it to a manifest.

Codingrules section 13: "Environment variables are read in exactly one place, are prefixed
`HIVEMIND_`, and only override manifest values." This package is that one place, for two kinds of
composition root that share nothing but the rule: the Hive Stand's, which loads a Hive Manifest and
lets the environment override it (`hive`), and a Cell's own Warden, which has no Hive Manifest and
starts from its variables alone (`in_cell`); `common` holds the one flag both read. This file is
the package's face: a caller writes `from hivemind.manifest.env import read_env` without knowing
the split (codingrules 5.2, 5.4).

Fits into the Hive:
    Layer 1 (foundational services; capacity as data), inside `hivemind.manifest`. Called by
    `hivemind.manifest.loader`, provider adapters, `hivemind.cli.in_cell` and
    `scripts/build_cell_image.py`. Calls into `hivemind.manifest.errors` and
    `hivemind.manifest.schema` only.

Key invariants:
    - This file holds re-exports and __all__ only; the modules behind it define every name.
    - Sibling modules inside the package import each other by full module path, never through
      this face.

See Also:
    - .claude/codingrules.md section 13 for the rule this package keeps.

Public API:
    - EnvOverrides, read_env, apply_env, provider_api_key, read_build_image_sha256_override,
      TUNNEL_VARIABLE_PREFIX: the Hive Stand's overrides and secrets (hive).
    - InCellEnv, read_in_cell_env: what a Cell's own Warden starts from (in_cell).
"""

from hivemind.manifest.env.hive import (
    TUNNEL_VARIABLE_PREFIX,
    EnvOverrides,
    apply_env,
    provider_api_key,
    read_build_image_sha256_override,
    read_env,
)
from hivemind.manifest.env.in_cell import InCellEnv, read_in_cell_env

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
