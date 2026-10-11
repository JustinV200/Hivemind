"""End-to-end: Nectar ripens into Honey with RIPENER and EMBEDDER both on the local server.

Phase 8's ripening criterion under `offline.toml`. A finding is deposited into the Hive's Honey
Store (the Hive's searchable knowledge base), one ripening pass summarises it on the RIPENER slot
and embeds every row on the EMBEDDER slot, both bound to providers the derived manifest points at
the scripted loopback server (`/v1/chat/completions` for the summary, `/v1/embeddings` for the
vectors, which the server computes as hashed bags of words so a paraphrase lands near its source).
Asserted: the SUMMARY row names the scripted ripener model (a heuristic summary would record none),
every row has a vector, a paraphrased question finds the finding with vectors in the ranking, every
`llm.call` is local, and no socket left loopback. The real-model counterpart, with RIPENER on
Ollama and EMBEDDER in process, is `tests/evals/honey/test_ripening_local.py` (`local_llm`).

Fits into the Hive:
    Test infrastructure (codingrules section 14.2), not shipped.

Key invariants:
    - The Hive is built but never run: ripening is the House Bee's pass, driven here by hand.

See Also:
    - tests/evals/honey/test_ripening_local.py for the same pass on real models.
    - tests/e2e/test_honey_compounds.py for the House Bee's pass inside a running Hive.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from e2e.offline import (
    EgressLog,
    assert_every_call_local,
    calls_on,
    offline_manifest,
    scripted_model_ids,
)
from e2e.scripted_openai import Scenario, serve_in_thread

from hivemind.cell import CombShieldLevel, HoneyClearance
from hivemind.cli.compose import Hive, build_hive
from hivemind.forage.slots import ModelSlot
from hivemind.honey_store import (
    HoneyPart,
    HoneyReader,
    HoneySearch,
    NectarOrigin,
    NectarSubmission,
    ReadFilter,
    queen_read_capabilities,
)
from hivemind.pheromone import MAX_QUERY_LIMIT, TrailQuery
from waggle.clock import SystemClock
from waggle.ids import new_cell_id
from waggle.messages.honey import NectarKind

pytestmark = pytest.mark.e2e

_FINDING = (
    "The widget factory's staging environment reads its configuration from "
    "/etc/widgets/staging.toml. After editing that file, restart the service with "
    "`systemctl restart widgets-staging`; the service refuses to start when the [db] section "
    "names a port below 1024, and it logs the refusal to /var/log/widgets/staging.log. The "
    "production environment uses /etc/widgets/production.toml and must never be restarted "
    "during business hours without the on-call engineer's approval."
)
_PARAPHRASE = "how do I restart the staging widget service after changing its settings?"
_EVERYTHING = ReadFilter(readable=("*",), max_clearance=HoneyClearance.C2)  # Lists every row.


def test_ripening_summarises_and_embeds_on_the_local_server_and_a_paraphrase_finds_it(
    tmp_path: Path, egress: EgressLog
) -> None:
    with serve_in_thread(Scenario()) as base_url:
        manifest, _path = offline_manifest(tmp_path, base_url)
        hive = build_hive(manifest, environ={}, clock=SystemClock())

        asyncio.run(_ripen_and_find(hive))
        events = asyncio.run(hive.stores.trail.query(TrailQuery(limit=MAX_QUERY_LIMIT)))

    assert_every_call_local(events, manifest)
    assert calls_on(events, ModelSlot.RIPENER) and calls_on(events, ModelSlot.EMBEDDER)
    egress.assert_clean_and_live()


async def _ripen_and_find(hive: Hive) -> None:
    """Deposit one finding, run one pass, and check the Honey and the search that finds it."""
    access = hive.honey
    assert access is not None, "build_hive wired no Honey Store"
    try:
        await access.intake.submit(_finding())
        outcome = await access.ripener.run_pass()

        assert (outcome.ripen.ripened, outcome.ripen.failed) == (1, 0), outcome
        rows = await access.store.list_honey(_EVERYTHING, scope_prefix=None, limit=10, offset=0)
        summary = next(row for row in rows if row.part is HoneyPart.SUMMARY)
        # Written by the RIPENER slot's model: a heuristic summary records no ripener model.
        assert summary.ripener_model == scripted_model_ids()["ripener"]
        stats = await access.store.stats()
        assert sum(stats.vectors_by_model.values()) == len(rows)  # Every row embedded.
        found = await access.retriever.search_outcome(_operator_search(hive))
        assert found.vector_used, found.response.reason
        assert found.response.hits, found.response.reason
    finally:
        await hive.registry.aclose()


def _finding() -> NectarSubmission:
    """The one verified finding, long enough for the RIPENER to summarise."""
    clock = SystemClock()
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


def _operator_search(hive: Hive) -> HoneySearch:
    """A paraphrased question, asked with the Queen's read-everything capabilities."""
    reader = HoneyReader(
        requester=hive.manifest.hive.id,
        capabilities=queen_read_capabilities(),
        ceiling=HoneyClearance.C2,
        is_night_veil=False,
    )
    return HoneySearch(
        text=_PARAPHRASE, reader=reader, requested_scopes=(), max_hits=5, max_tokens=2_000
    )
