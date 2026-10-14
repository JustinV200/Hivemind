"""Hold what both halves of the environment reader share: one boolean flag's rule and a config.

`HIVEMIND_LLM_OFFLINE` means the same on the Hive Stand and in a Cell, so the one parser that
decides what the variable may hold lives here, read by `hivemind.manifest.env.hive` and
`hivemind.manifest.env.in_cell` alike, beside the frozen model config every value they build uses.

Fits into the Hive:
    Layer 1 (foundational services; capacity as data), inside `hivemind.manifest.env`. Imported by
    its two sibling modules only. Calls into `hivemind.manifest.errors` and pydantic only.

Key invariants:
    - `read_offline_flag` accepts only "1", "true" or "yes" (case-insensitive) as true; any other
      value the variable is actually set to is a ManifestError, not a silently-ignored override:
      an operator who sets it expects it to take effect, and "false" or "0" would invite the
      mistaken belief that unsetting the flag this way turns offline mode off.

See Also:
    - .claude/codingrules.md section 13 for "environment variables are read in exactly one place".
"""

from __future__ import annotations

from collections.abc import Mapping

from pydantic import ConfigDict

from hivemind.manifest.errors import ManifestError

# Every value HIVEMIND_LLM_OFFLINE may take to mean true; anything else the variable is actually
# set to is a typo worth surfacing rather than silently treating as false (see module docstring).
_TRUE_BOOL_LITERALS = frozenset({"1", "true", "yes"})

# A frozen, extras-forbidding config matching every other boundary value (codingrules section 8.5).
MODEL_CONFIG = ConfigDict(frozen=True, extra="forbid")

__all__ = ["MODEL_CONFIG", "read_offline_flag"]


def read_offline_flag(environ: Mapping[str, str]) -> bool | None:
    """Parse HIVEMIND_LLM_OFFLINE, or return None when it is not set.

    Args:
        environ: A raw environment mapping; never `os.environ` read here (codingrules section 13).

    Returns:
        True when the variable holds one of the true spellings, None when it is not set.

    Raises:
        ManifestError: The variable is set to anything else.
    """
    raw = environ.get("HIVEMIND_LLM_OFFLINE")
    if raw is None:
        return None
    if raw.strip().lower() in _TRUE_BOOL_LITERALS:
        return True
    raise ManifestError(
        f"HIVEMIND_LLM_OFFLINE={raw!r} is not a recognised boolean "
        f"(expected one of {sorted(_TRUE_BOOL_LITERALS)}, case-insensitive)."
    )
