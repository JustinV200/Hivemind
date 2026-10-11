"""Derive the offline manifest for a real local model server, from the operator's environment.

The `local_llm` job and an operator's own machine run the offline proof against a real server
(Ollama, vLLM, llama.cpp), not the scripted one. The shipped `docs/manifests/offline.toml` names two
chat models (a larger one for the Queen, the Judge and the Warden; a smaller for the rest) and
whichever the operator has pulled may differ, so this module reads the same kind of environment
variables the other `local_llm` tests read and changes only what they name: the chat server's
`base_url` (`HIVEMIND_LOCAL_LLM_BASE_URL`), the larger and smaller chat model ids
(`HIVEMIND_LOCAL_LARGE_MODEL`, `HIVEMIND_LOCAL_SMALL_MODEL`), and optionally the Whisper size
(`HIVEMIND_LOCAL_WHISPER_MODEL`) and an embedding model served by the chat server instead of the
in-process one (`HIVEMIND_LOCAL_EMBED_MODEL`). The in-process embedder and transcriber are otherwise
kept as shipped, so a run with the `whisper` extra and a cached model exercises them. The result is
loaded through `load_manifest`, so `offline = true` and its loopback rule apply as they do for
an operator.

Fits into the Hive:
    Test infrastructure (codingrules section 14.5), not shipped. Used by `tests/evals/offline/`.
    Calls into `e2e.offline.manifest`.

Key invariants:
    - No model id or address is written here: each comes from the shipped file or the environment.
    - A variable that is unset leaves the shipped value in place.

See Also:
    - tests/evals/honey/test_ripening_local.py for the variables this shares.
    - .github/workflows/integration.yml for where the job sets them.
"""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from pathlib import Path
from typing import Any

from e2e.offline.manifest import shipped_offline_manifest, write_and_load
from e2e.scripted_openai import SLOT_MODELS

from hivemind.manifest import HiveManifest

__all__ = ["live_offline_manifest"]


def live_offline_manifest(
    directory: Path,
    environ: Mapping[str, str],
    extra: Mapping[str, Any] | None = None,
    planner_url: str | None = None,
) -> tuple[HiveManifest, Path]:
    """Derive the offline manifest for the server `environ` names; write it and load it.

    Args:
        directory: The test's own directory.
        environ: The process environment (read here only; never `os.environ` implicitly).
        extra: Tables merged over the pinned ones (the Hive Stand's access level, say).
        planner_url: When set, a scripted server's URL to put the deciding slots on (Queen,
            Attendant, Warden, Judge), so a scenario's plan is exact and only the Worker is the
            real model.

    Returns:
        The loaded manifest and the path of the TOML it was loaded from.
    """
    data = shipped_offline_manifest()
    data["llm"]["providers"]["local"]["base_url"] = environ["HIVEMIND_LOCAL_LLM_BASE_URL"]
    slots = data["llm"]["slots"]
    # Keyed by the shipped model id each variable replaces; an unset variable renames nothing.
    renames = {
        slots["queen"]["model"]: environ.get("HIVEMIND_LOCAL_LARGE_MODEL"),
        slots["worker"]["model"]: environ.get("HIVEMIND_LOCAL_SMALL_MODEL"),
        slots["transcriber"]["model"]: environ.get("HIVEMIND_LOCAL_WHISPER_MODEL"),
    }
    _rename_models(data, {old: new for old, new in renames.items() if new})
    if environ.get("HIVEMIND_LOCAL_EMBED_MODEL"):
        _embed_on_the_server(data, environ)
    if planner_url is not None:
        _decide_on_a_scripted_server(data, planner_url)
    return write_and_load(data, directory, extra)


def _rename_models(data: dict[str, Any], renames: Mapping[str, str]) -> None:
    """Rename every slot binding and `[forage.map]` row on a model the environment replaces."""
    rows = [*data["llm"]["slots"].values(), *data["forage"]["map"].values()]
    for row in rows:
        row["model"] = renames.get(row["model"], row["model"])


def _embed_on_the_server(data: dict[str, Any], environ: Mapping[str, str]) -> None:
    """Serve the EMBEDDER from the chat server (an embedding model pulled onto it)."""
    base_url = environ["HIVEMIND_LOCAL_LLM_BASE_URL"]
    model = environ["HIVEMIND_LOCAL_EMBED_MODEL"]
    data["llm"]["providers"]["embedded"] = {"kind": "openai_compat", "base_url": base_url}
    _rename_models(data, {data["llm"]["slots"]["embedder"]["model"]: model})


def _decide_on_a_scripted_server(data: dict[str, Any], base_url: str) -> None:
    """Bind the deciding slots to a scripted loopback server; the Worker stays on the model."""
    providers = data["llm"]["providers"]
    providers["scripted"] = {
        "kind": "openai_compat",
        "base_url": base_url,
        "seats": 4,
        "capabilities": deepcopy(providers["local"]["capabilities"]),
    }
    for key in ("queen", "attendant", "warden", "judge"):
        data["llm"]["slots"][key] = {"provider": "scripted", "model": SLOT_MODELS[key]}
