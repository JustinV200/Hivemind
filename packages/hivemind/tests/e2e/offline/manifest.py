"""Derive the offline proof's manifest from `docs/manifests/offline.toml` for the scripted server.

The offline proof must run against the shipped file, not a lookalike, so a change to
`offline.toml` (a slot rebound, a source added, a grade moved) reaches every case here. This module
reads that file, changes only what a machine with no model server and no Whisper weights forces,
and writes the result beside the test's data so `hive run` can load it too. What changes: the one
chat provider's `base_url` (the scripted server on a free loopback port), the embedder and the
transcriber (in-process kinds in the shipped file, which need weights this container cannot fetch)
become `openai_compat` rows on the same scripted server, every model id becomes the scripted one
that routes to a script, and the Hive Stand section the shipped file leaves to its defaults is
pinned (a database and scratch root under the test's directory, 64 cores, short cadences). What
stays: `[llm] offline = true`, the Hive and node ids, every slot's provider name, the
`[forage.map]` rows and their grades, the capability overrides of the weak local model, and
`[forage.roles.drone]`.

Fits into the Hive:
    Test infrastructure (codingrules section 14.5), not shipped. Used by every module of
    `tests/e2e/offline/`. Calls into `hivemind.manifest` (`load_manifest`) and the scripted server's
    slot-to-model table.

Key invariants:
    - The derived file is loaded through `load_manifest`, so the schema's offline check (a
      non-loopback `base_url` refuses to load) runs on it exactly as it would on an operator's.
    - Nothing here names a model id or a server address as a literal: both come from the shipped
      manifest or from the scripted server.

See Also:
    - docs/manifests/offline.toml for the file this derives from.
    - tests/e2e/scripted_openai.py for the server the derived manifest points at.
"""

from __future__ import annotations

import json
import tomllib
from collections.abc import Mapping
from copy import deepcopy
from pathlib import Path
from typing import Any

from e2e.scripted_openai import SLOT_MODELS

from hivemind.manifest import HiveManifest, load_manifest

OFFLINE_MANIFEST = Path(__file__).resolve().parents[5] / "docs" / "manifests" / "offline.toml"
HIVE_STAND_CORES = 64  # Pinned so a busy test host never zeroes a Drone's grant (kernel suite).
# The two slots the offline proof routes to a script of their own: the ripener's summary and the
# embedder's vectors. Every other slot keeps the model id `scripted_openai.write_manifest` gives it.
SCRIPTED_RIPENER = "scripted-ripener"
SCRIPTED_EMBEDDER = "scripted-embedder"
_ROW_OWNERS = ("worker", "queen", "embedder", "transcriber")  # See `_bind_scripted_models`.
# The Hive Stand, the Queen's cadence and the heartbeat, in the short form every e2e manifest uses.
_CADENCE: dict[str, Any] = {
    "queen": {"tick_interval_s": 0.05, "heartbeat_interval_s": 0.5},
    "supervision": {"heartbeat_interval_s": 0.5, "heartbeat_miss_limit": 6},
}

__all__ = [
    "HIVE_STAND_CORES",
    "OFFLINE_MANIFEST",
    "SCRIPTED_EMBEDDER",
    "SCRIPTED_RIPENER",
    "dump_toml",
    "offline_manifest",
    "scripted_model_ids",
    "shipped_offline_manifest",
    "write_and_load",
]


def scripted_model_ids() -> dict[str, str]:
    """Return each slot's manifest key mapped to the scripted model id the offline proof binds."""
    return {**SLOT_MODELS, "ripener": SCRIPTED_RIPENER, "embedder": SCRIPTED_EMBEDDER}


def offline_manifest(
    directory: Path,
    base_url: str,
    extra: Mapping[str, Any] | None = None,
    capabilities: Mapping[str, Any] | None = None,
) -> tuple[HiveManifest, Path]:
    """Derive the offline manifest for `base_url`, write it under `directory`, and load it.

    Args:
        directory: The test's own directory; `hive.toml`, `data/` and `scratch/` go inside it.
        base_url: The scripted server's `/v1` URL (loopback).
        extra: Tables merged over the derived ones (a `[placement]` section, say), by key.
        capabilities: Fields set on the chat provider's `[llm.providers.local.capabilities]`
            (for example `vision`), over what the shipped file declares.

    Returns:
        The loaded manifest and the path of the TOML it was loaded from.
    """
    data = shipped_offline_manifest()
    _point_providers_at(data["llm"]["providers"], base_url)
    data["llm"]["providers"]["local"]["capabilities"].update(capabilities or {})
    _bind_scripted_models(data)
    return write_and_load(data, directory, extra)


def shipped_offline_manifest() -> dict[str, Any]:
    """Return `docs/manifests/offline.toml` parsed, for a caller to edit what its run forces."""
    return tomllib.loads(OFFLINE_MANIFEST.read_text(encoding="utf-8"))


def write_and_load(
    data: dict[str, Any], directory: Path, extra: Mapping[str, Any] | None = None
) -> tuple[HiveManifest, Path]:
    """Pin the Hive Stand under `directory`, write `data` as `hive.toml` there and load it.

    Args:
        data: The (edited) offline manifest, as parsed TOML.
        directory: Where `hive.toml`, `data/` and `scratch/` go.
        extra: Tables merged over the pinned ones, by key.

    Returns:
        The loaded manifest, validated like an operator's, and the file it came from.
    """
    (directory / "data").mkdir(parents=True, exist_ok=True)
    (directory / "scratch").mkdir(parents=True, exist_ok=True)
    data["hive"]["db"] = (directory / "data" / "hive.sqlite3").as_posix()
    data["hive_stand"] = {
        "enabled": True,
        "scratch_root": (directory / "scratch").as_posix(),
        "capacity": {"cores": HIVE_STAND_CORES},
    }
    for table, values in {**deepcopy(_CADENCE), **(extra or {})}.items():
        data[table] = {**data.get(table, {}), **values}
    path = directory / "hive.toml"
    path.write_text(dump_toml(data), encoding="utf-8")
    return load_manifest(path, {}), path


def _point_providers_at(providers: dict[str, Any], base_url: str) -> None:
    """Send the chat provider, and the two in-process ones, to the scripted server's URL."""
    providers["local"]["base_url"] = base_url
    # The shipped file runs these two in process; here the scripted server stands in for the
    # model each would load, over the same `openai_compat` adapter a local Whisper or embedding
    # server would use (the options of the in-process kind do not apply and are dropped).
    for name in ("embedded", "hearing"):
        seats = providers[name].get("seats", 1)
        providers[name] = {"kind": "openai_compat", "base_url": base_url, "seats": seats}


def _bind_scripted_models(data: dict[str, Any]) -> None:
    """Replace each slot's model id, and its `[forage.map]` row's, by the scripted id."""
    ids = scripted_model_ids()
    slots = data["llm"]["slots"]
    original = {key: row["model"] for key, row in slots.items()}
    # A model several slots share (the 3B one) keeps one map row; the first of these slots in
    # `_ROW_OWNERS` order names it, so that row's scripted id is the Drone's, the slot that matters
    # to Forage and routing, rather than whichever slot the file lists first.
    renamed: dict[str, str] = {}
    for key in _ROW_OWNERS:
        renamed.setdefault(original[key], ids[key])
    for key, row in slots.items():
        row["model"] = ids[key]
    for source in data["forage"]["map"].values():
        source["model"] = renamed.get(source["model"], source["model"])


# ──────────────────────────────────────────────────────────────────────────────
# A small TOML writer (the standard library reads TOML, never writes it)
# ──────────────────────────────────────────────────────────────────────────────


def dump_toml(data: Mapping[str, Any], prefix: tuple[str, ...] = ()) -> str:
    """Render a nested mapping as TOML: scalars and lists first, then each sub-table by header."""
    scalars = [(k, v) for k, v in data.items() if not isinstance(v, Mapping)]
    tables = [(k, v) for k, v in data.items() if isinstance(v, Mapping)]
    lines = [f"{key} = {_value(value)}" for key, value in scalars]
    head = f"[{'.'.join(prefix)}]\n" if prefix and scalars else ""
    out = head + "\n".join(lines) + ("\n\n" if lines else "")
    for key, table in tables:
        out += dump_toml(table, (*prefix, key))
    return out


def _value(value: object) -> str:
    """Render one scalar or list: JSON's spelling of strings, numbers and lists is valid TOML."""
    if isinstance(value, bool):
        return "true" if value else "false"
    return json.dumps(value)
