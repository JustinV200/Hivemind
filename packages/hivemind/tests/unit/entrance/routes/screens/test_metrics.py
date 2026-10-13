"""Test hivemind.entrance.routes.screens.metrics: the Prometheus endpoint over a real listener.

The route answers the exposition text with its exact media type, carrying the first-class counters
and a labelled series; the document declares that media type for the success and JSON for the
refusals; a device without ``observe`` is refused.

Fits into the Hive:
    Mirrors src/hivemind/entrance/routes/screens/metrics.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.
"""

from __future__ import annotations

import json
from typing import Any

from builders.entrance.screens import heartbeat
from builders.entrance.serving import ProgramGrant, serving

from hivemind.entrance.landing_board import render_document
from waggle.messages.supervision import WorkerState


async def test_the_endpoint_answers_exposition_text_with_the_counters() -> None:
    async with serving() as rig:
        client, session = await rig.program(ProgramGrant(capabilities=("observe",)))
        link = rig.queen.wardens[0]
        rig.telemetry.record(
            link.warden_id, heartbeat([("", WorkerState.RUNNING)]), rig.clock.now()
        )

        scraped = await client.call(session, "GET", "/v1/metrics")

    assert scraped.status_code == 200, scraped.text
    assert scraped.headers["content-type"] == "text/plain; version=0.0.4; charset=utf-8"
    lines = scraped.text.splitlines()
    assert "# TYPE hive_bees_active gauge" in lines
    assert "hive_bees_active 2" in lines
    assert "hive_cells_active 1" in lines and "hive_cells_real 1" in lines
    assert 'hive_cells_total{kind="real",status="active"} 1' in lines
    assert 'hive_wardens{location="hive_stand"} 1' in lines
    assert scraped.text.endswith("\n")


async def test_a_submit_only_device_is_refused_the_metrics() -> None:
    async with serving() as rig:
        client, session = await rig.program(ProgramGrant(capabilities=("entrance:submit",)))

        refused = await client.call(session, "GET", "/v1/metrics")

    assert refused.status_code == 403


def test_the_document_declares_the_text_media_type_and_json_refusals() -> None:
    document: Any = json.loads(render_document())

    responses = document["paths"]["/v1/metrics"]["get"]["responses"]

    assert list(responses["200"]["content"]) == ["text/plain; version=0.0.4; charset=utf-8"]
    assert list(responses["403"]["content"]) == ["application/json"]
