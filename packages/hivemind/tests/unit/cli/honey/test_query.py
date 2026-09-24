"""Tests for hivemind.cli.honey.query: `hive honey query` and `hive honey stats`.

Fits into the Hive:
    Mirrors src/hivemind/cli/honey/query.py (codingrules section 3). Drives the real typer app
    against a real fake-provider manifest and SQLite file (`harness`): Nectar is seeded through
    the real intake and ripened by `hive honey ripen --now` itself.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.cli.honey.query for the module under test.
"""

from __future__ import annotations

import json
from pathlib import Path

from unit.cli.honey.harness import bind_embedder, invoke, make_hive, seed_nectar, trail_events

from hivemind.cell import HoneyClearance
from waggle.clock import SystemClock
from waggle.ids import new_task_id
from waggle.messages.honey import NectarKind

_STAGING = "The widget factory's staging config lives at /etc/widgets/staging.toml."


def _ripened_hive(tmp_path: Path) -> Path:
    """Seed three Nectar (C1 shared, C2 shared, C1 task material) and ripen them."""
    manifest = make_hive(tmp_path)
    seed_nectar(manifest, _STAGING)
    seed_nectar(
        manifest, "The deploy key for staging rotates on Mondays.", declared=HoneyClearance.C2
    )
    seed_nectar(
        manifest,
        "Transcript: copied the staging config before editing it.",
        kind=NectarKind.TRANSCRIPT,
        task_id=new_task_id(SystemClock()),
    )
    assert invoke(manifest, "ripen", "--now").exit_code == 0
    return manifest


def test_query_prints_ranked_hits_and_records_one_queried_event(tmp_path: Path) -> None:
    manifest = _ripened_hive(tmp_path)

    result = invoke(manifest, "query", "staging config")

    assert result.exit_code == 0, result.output
    assert "Hybrid search" in result.stdout
    assert "/hive/honey_" in result.stdout
    assert "/etc/widgets/staging.toml" in result.stdout
    assert "embedder: test-model" in result.stdout
    (event,) = trail_events(manifest, "honey.queried")
    assert event.payload["hits"] == 3


def test_query_json_is_the_retrievers_response(tmp_path: Path) -> None:
    manifest = _ripened_hive(tmp_path)

    result = invoke(manifest, "query", "staging config", "--max-hits", "2", "--json")

    assert result.exit_code == 0, result.output
    response = json.loads(result.stdout)
    assert len(response["hits"]) == 2
    assert response["hits"][0]["honey_ref"].startswith("/hive/")


def test_query_reads_within_the_groups_clearance(tmp_path: Path) -> None:
    manifest = _ripened_hive(tmp_path)

    result = invoke(manifest, "--clearance", "C1", "query", "deploy key rotates", "--json")

    assert result.exit_code == 0, result.output
    response = json.loads(result.stdout)
    assert all(hit["clearance"] != "C2" for hit in response["hits"])
    assert response["filtered_count"] >= 1


def test_query_narrows_to_the_given_scopes(tmp_path: Path) -> None:
    manifest = _ripened_hive(tmp_path)

    result = invoke(manifest, "query", "staging config", "--scope", "hive", "--json")

    assert result.exit_code == 0, result.output
    assert {hit["scope"] for hit in json.loads(result.stdout)["hits"]} == {"hive"}


def test_query_refuses_a_malformed_scope_before_opening_anything(tmp_path: Path) -> None:
    manifest = make_hive(tmp_path)

    result = invoke(manifest, "query", "anything", "--scope", "everything")

    assert result.exit_code == 2
    assert "not a valid Honey Store scope" in result.stderr


def test_query_without_an_embedder_searches_full_text_and_says_why(tmp_path: Path) -> None:
    manifest = _ripened_hive(tmp_path)
    bind_embedder(manifest, hosted=True)

    result = invoke(manifest, "query", "staging config")

    assert result.exit_code == 0, result.output
    assert "Full-text search only (no embedder is bound)" in result.stdout
    assert "embedder: none (hivemind.llm.embedding_unsupported" in result.stdout


def test_stats_counts_nectar_honey_labels_scopes_and_vectors(tmp_path: Path) -> None:
    manifest = _ripened_hive(tmp_path)

    text = invoke(manifest, "stats")
    as_json = invoke(manifest, "stats", "--json")

    assert text.exit_code == 0, text.output
    assert "RIPENED=3" in text.stdout
    assert "test-model=3" in text.stdout
    stats = json.loads(as_json.stdout)
    assert stats["nectar_by_state"] == {"RIPENED": 3}
    assert stats["vectors_by_model"] == {"test-model": 3}
    assert stats["honey_by_scope_kind"] == {"hive": 2, "task": 1}
