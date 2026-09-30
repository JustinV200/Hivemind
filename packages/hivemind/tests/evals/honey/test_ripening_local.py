"""local_llm: Nectar ripens into Honey with RIPENER and EMBEDDER on a local model server.

Roadmap phase 7's third exit criterion: "Ripening runs with RIPENER and EMBEDDER on local adapters
in the local_llm job." Every binding comes from `docs/manifests/local.toml` (one
OpenAI-compatible server on loopback, `offline = true`) through the same composition helper a
running Hive uses (`hivemind.cli.compose.honey.build_honey_access`), so this exercises the real
adapters, the structured-output ladder on a JSON-mode local model, the Fanner's metering and the
store end to end: a finding is deposited, one ripening pass summarises it on the RIPENER slot and
embeds it on the EMBEDDER slot, and a paraphrased query finds it with vectors in the ranking.

Gated like `tests.evals.handoff.test_handoff_eval_live`: skips cleanly unless
`HIVEMIND_LIVE_LLM=1` and `HIVEMIND_LOCAL_LLM_BASE_URL` are set. The base URL replaces the
manifest's own; `HIVEMIND_LOCAL_RIPENER_MODEL` and `HIVEMIND_LOCAL_EMBED_MODEL` optionally replace
the two slots' model ids for a server that hosts different ones. No model id or URL is written in
this file (`scripts/check_no_model_ids.py`).

Fits into the Hive:
    Test infrastructure (codingrules section 14.2), not shipped.

Key invariants:
    - None: this module holds tests only.

See Also:
    - .claude/roadmap.md phase 7's exit criteria.
    - hivemind.cli.compose.honey for the builder under test.
    - tests/evals/README.md for how to run it.
"""

from __future__ import annotations

import importlib.util
import os
from collections.abc import Mapping
from pathlib import Path

import pytest

from hivemind.cell import CombShieldLevel, HoneyClearance
from hivemind.cli.compose.deps import build_fanner
from hivemind.cli.compose.honey import build_honey_access, resolve_embedder, resolve_ripener
from hivemind.cli.stores import build_forage_map, build_registry
from hivemind.common.sqlite import connect
from hivemind.honey_store import (
    HoneyAccess,
    HoneyPart,
    HoneyReader,
    HoneySearch,
    NectarOrigin,
    NectarSubmission,
    ReadFilter,
    SqliteHoneyStore,
    queen_read_capabilities,
)
from hivemind.llm import ProviderRegistry
from hivemind.manifest import HiveManifest, load_manifest
from hivemind.manifest.schema.llm import ProviderSpec, SlotBinding
from hivemind.pheromone import SqlitePheromoneTrail
from waggle.clock import SystemClock
from waggle.ids import new_cell_id
from waggle.messages.honey import NectarKind

_REPO_ROOT = Path(__file__).resolve().parents[5]
_LOCAL_MANIFEST = _REPO_ROOT / "docs" / "manifests" / "local.toml"
_LOCAL_PROVIDER = "local"  # The one provider docs/manifests/local.toml declares.
_IN_PROCESS_PROVIDER = "in_process"  # Added for the in-process EMBEDDER case below.
_FINDING = (
    "The widget factory's staging environment reads its configuration from "
    "/etc/widgets/staging.toml. After editing that file, restart the service with "
    "`systemctl restart widgets-staging`; the service refuses to start when the [db] section "
    "names a port below 1024, and it logs the refusal to /var/log/widgets/staging.log. The "
    "production environment uses /etc/widgets/production.toml and must never be restarted "
    "during business hours without the on-call engineer's approval, because the restart drops "
    "every open order stream for about forty seconds."
)
_PARAPHRASE = "how do I bring the staging widget service back up after changing its settings?"
_EVERYTHING = ReadFilter(readable=("*",), max_clearance=HoneyClearance.C2)  # Lists every row.


def _local_manifest(environ: Mapping[str, str]) -> HiveManifest:
    """Load local.toml, pointed at the operator's own server and, optionally, its own models."""
    manifest = load_manifest(_LOCAL_MANIFEST)
    llm = manifest.llm
    providers = dict(llm.providers)
    providers[_LOCAL_PROVIDER] = providers[_LOCAL_PROVIDER].model_copy(
        update={"base_url": environ["HIVEMIND_LOCAL_LLM_BASE_URL"]}
    )
    slots = dict(llm.slots)
    # Either slot's model may be replaced for a server that hosts different ones.
    for key, variable in (
        ("ripener", "HIVEMIND_LOCAL_RIPENER_MODEL"),
        ("embedder", "HIVEMIND_LOCAL_EMBED_MODEL"),
    ):
        if environ.get(variable):
            slots[key] = SlotBinding(provider=_LOCAL_PROVIDER, model=environ[variable])
    update = {"providers": providers, "slots": slots}
    return manifest.model_copy(update={"llm": llm.model_copy(update=update)})


def _with_in_process_embedder(manifest: HiveManifest, model: str) -> HiveManifest:
    """Rebind EMBEDDER to a `sentence_transformers` provider running `model` in this process.

    `model` is a sentence-transformers model name or a local path; local.toml's `offline = true`
    makes the adapter load it with `local_files_only`, so it never reaches a model hub.
    """
    llm = manifest.llm
    providers = dict(llm.providers)
    providers[_IN_PROCESS_PROVIDER] = ProviderSpec(kind="sentence_transformers")
    slots = dict(llm.slots)
    slots["embedder"] = SlotBinding(provider=_IN_PROCESS_PROVIDER, model=model)
    update = {"providers": providers, "slots": slots}
    return manifest.model_copy(update={"llm": llm.model_copy(update=update)})


async def _local_access(
    tmp_path: Path, manifest: HiveManifest
) -> tuple[HoneyAccess, ProviderRegistry]:
    """Build the Hive's own Honey Store handles on a fresh file, both local bindings live.

    Returns the registry too: the test closes it, since a real server keeps its connections alive.
    """
    clock = SystemClock()  # Real models have real latency; nothing here fakes time.
    connection = connect(tmp_path / "hive.sqlite3")
    trail = await SqlitePheromoneTrail.create(connection, clock)
    store = await SqliteHoneyStore.create(connection, clock)
    forage_map = build_forage_map(manifest, clock)
    registry = build_registry(manifest, os.environ, clock, forage_map=forage_map)
    # Both bindings must be live: a degraded one would pass the pass below for the wrong reason.
    assert resolve_ripener(registry) is not None
    assert resolve_embedder(registry) is not None
    fanner = build_fanner(manifest, forage_map, trail, clock)
    return build_honey_access(manifest, store, registry, fanner, clock), registry


def _finding(clock: SystemClock) -> NectarSubmission:
    """The one verified finding the test ripens: long enough for the RIPENER to summarise."""
    return NectarSubmission(
        kind=NectarKind.FINDING,
        origin=NectarOrigin.TASK_OUTCOME,
        media_type="text/markdown",
        title="Widget staging service",
        content=_FINDING.encode(),
        task_id=None,
        cell_id=new_cell_id(clock),
        observed_at=clock.now(),
        declared=HoneyClearance.C1,
        from_borrowed_cell=False,
        tier=CombShieldLevel.MEADOW,
    )


def _operator_search(manifest: HiveManifest) -> HoneySearch:
    """A paraphrased question, asked with the operator's own read-everything capabilities."""
    reader = HoneyReader(
        requester=manifest.hive.id,
        capabilities=queen_read_capabilities(),
        ceiling=HoneyClearance.C2,
        is_night_veil=False,
    )
    return HoneySearch(
        text=_PARAPHRASE, reader=reader, requested_scopes=(), max_hits=5, max_tokens=2_000
    )


@pytest.mark.local_llm
async def test_ripening_summarises_and_embeds_on_local_models(tmp_path: Path) -> None:
    """Skips cleanly unless HIVEMIND_LIVE_LLM=1 and a local server base URL are set."""
    if os.environ.get("HIVEMIND_LIVE_LLM") != "1":
        pytest.skip("HIVEMIND_LIVE_LLM is not set to '1'.")
    if not os.environ.get("HIVEMIND_LOCAL_LLM_BASE_URL"):
        pytest.skip("HIVEMIND_LOCAL_LLM_BASE_URL must be set.")
    manifest = _local_manifest(os.environ)
    access, registry = await _local_access(tmp_path, manifest)
    try:
        await _ripen_and_find(access, manifest)
    finally:
        # A real server keeps connections alive; closed here, in this loop, the run leaves none.
        await registry.aclose()


async def _ripen_and_find(access: HoneyAccess, manifest: HiveManifest) -> None:
    """Ripen one finding on the local models, then find it by a paraphrase with vectors in use."""
    await access.intake.submit(_finding(SystemClock()))
    outcome = await access.ripener.run_pass()

    assert outcome.ripen.ripened == 1, outcome
    assert outcome.ripen.failed == 0, outcome
    rows = await access.store.list_honey(_EVERYTHING, scope_prefix=None, limit=10, offset=0)
    summary = next(row for row in rows if row.part is HoneyPart.SUMMARY)
    # The RIPENER wrote it: a heuristic summary never records a ripener model.
    assert summary.ripener_model is not None
    stats = await access.store.stats()
    assert sum(stats.vectors_by_model.values()) == len(rows)  # Every row embedded while ripening.
    found = await access.retriever.search_outcome(_operator_search(manifest))
    assert found.vector_used, found.response.reason
    assert found.response.hits, found.response.reason
    assert found.response.hits[0].honey_ref.startswith("/hive/")


@pytest.mark.local_llm
async def test_ripening_embeds_in_process_with_sentence_transformers(tmp_path: Path) -> None:
    """The same pass with EMBEDDER on the in-process adapter; RIPENER stays on the server.

    Skips cleanly unless the gates above are set, `HIVEMIND_LOCAL_ST_EMBED_MODEL` names a model
    (a name or a local path) and the optional `embeddings` extra is installed.
    """
    if os.environ.get("HIVEMIND_LIVE_LLM") != "1":
        pytest.skip("HIVEMIND_LIVE_LLM is not set to '1'.")
    if not os.environ.get("HIVEMIND_LOCAL_LLM_BASE_URL"):
        pytest.skip("HIVEMIND_LOCAL_LLM_BASE_URL must be set.")
    model = os.environ.get("HIVEMIND_LOCAL_ST_EMBED_MODEL")
    if not model:
        pytest.skip("HIVEMIND_LOCAL_ST_EMBED_MODEL must name a sentence-transformers model.")
    if importlib.util.find_spec("sentence_transformers") is None:
        pytest.skip("the optional embeddings extra (sentence-transformers) is not installed.")
    manifest = _with_in_process_embedder(_local_manifest(os.environ), model)
    access, registry = await _local_access(tmp_path, manifest)
    try:
        await _ripen_and_find(access, manifest)
    finally:
        await registry.aclose()
