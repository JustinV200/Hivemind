"""Tests for hivemind.manifest.schema.honey: the Honey Store's four `[honey.*]` tables.

Fits into the Hive:
    Mirrors src/hivemind/manifest/schema/honey.py (codingrules section 3: tests/unit mirrors
    src/ one-to-one).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.manifest.schema.honey for the module under test.
    - hivemind.manifest.schema.security for HoneySection, which nests all three.
"""

from __future__ import annotations

import tomllib

import pytest
from pydantic import ValidationError

from hivemind.manifest.schema.honey import (
    DEFAULT_MAX_JUDGE_CHARS,
    MAX_JUDGE_CHARS_CEILING,
    MAX_NECTAR_BYTES_CEILING,
    HoneyLoweringSection,
    HoneyRetrievalSection,
    HoneyRipeningSection,
    HoneyStoreSection,
)
from hivemind.manifest.schema.security import HoneySection
from waggle.messages.honey.exchange import DEFAULT_MAX_NECTAR_BYTES


def test_store_section_defaults_to_the_wire_deposit_cap() -> None:
    assert HoneyStoreSection().max_nectar_bytes == DEFAULT_MAX_NECTAR_BYTES


@pytest.mark.parametrize("value", [0, MAX_NECTAR_BYTES_CEILING + 1])
def test_store_section_rejects_a_cap_outside_its_bounds(value: int) -> None:
    with pytest.raises(ValidationError):
        HoneyStoreSection(max_nectar_bytes=value)


def test_ripening_section_defaults_chunk_with_a_shorter_overlap() -> None:
    section = HoneyRipeningSection()

    assert section.chunk_overlap_chars < section.chunk_chars
    assert section.summarise is True
    assert 0 < section.near_duplicate_similarity <= 1


@pytest.mark.parametrize("overlap", [1_600, 2_000])
def test_ripening_section_rejects_an_overlap_as_long_as_a_chunk(overlap: int) -> None:
    # Chunking must advance: an overlap as long as the chunk itself would repeat forever.
    with pytest.raises(ValidationError, match="shorter than chunk_chars"):
        HoneyRipeningSection(chunk_chars=1_600, chunk_overlap_chars=overlap)


@pytest.mark.parametrize(
    "changes",
    [
        {"chunk_chars": 199},
        {"chunk_chars": 8_001},
        {"interval_s": 0},
        {"embed_batch": 257},
        {"near_duplicate_similarity": 1.5},
        {"max_attempts": 0},
    ],
)
def test_ripening_section_rejects_out_of_range_values(changes: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        HoneyRipeningSection.model_validate(changes)


def test_retrieval_section_defaults_weigh_vectors_above_text() -> None:
    section = HoneyRetrievalSection()

    assert section.vector_weight > section.fts_weight > 0
    assert section.precheck_max_hits > 0


def test_retrieval_section_rejects_two_zero_weights() -> None:
    # A fused score divides by the sum of the weights.
    with pytest.raises(ValidationError, match="cannot both be zero"):
        HoneyRetrievalSection(fts_weight=0.0, vector_weight=0.0)


def test_retrieval_section_accepts_one_zero_weight() -> None:
    # Full-text only is a legitimate operator choice (no embedder at all).
    section = HoneyRetrievalSection(fts_weight=1.0, vector_weight=0.0)

    assert section.vector_weight == 0.0


@pytest.mark.parametrize(
    "changes",
    [
        {"budget_fraction": 0.0},
        {"budget_fraction": 0.6},
        {"precheck_max_hits": 17},
        {"min_score": 1.5},
        {"candidate_multiplier": 0},
    ],
)
def test_retrieval_section_rejects_out_of_range_values(changes: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        HoneyRetrievalSection.model_validate(changes)


def test_honey_section_nests_all_five_sub_tables_from_toml() -> None:
    document = tomllib.loads(
        """
        [clearance]
        default_label = "C1"
        [store]
        max_nectar_bytes = 1048576
        [ripening]
        interval_s = 5.0
        [retrieval]
        precheck_max_hits = 3
        [lowering]
        enabled = false
        """
    )

    section = HoneySection.model_validate(document)

    assert section.store.max_nectar_bytes == 1_048_576
    assert section.ripening.interval_s == 5.0
    assert section.retrieval.precheck_max_hits == 3
    assert section.lowering.enabled is False


def test_every_section_forbids_unknown_keys() -> None:
    models = (HoneyStoreSection, HoneyRipeningSection, HoneyRetrievalSection, HoneyLoweringSection)
    for model in models:
        with pytest.raises(ValidationError):
            model.model_validate({"surprise": 1})


def test_lowering_section_defaults_let_the_judge_review_what_the_ripener_read() -> None:
    section = HoneyLoweringSection()

    assert section.enabled is True
    assert section.max_judge_chars == DEFAULT_MAX_JUDGE_CHARS
    # The judge sees at least as much of a deposit as the ripener did (ADR-0034).
    assert section.max_judge_chars >= HoneyRipeningSection().summarise_max_input_chars
    assert (section.max_proposals_per_pass, section.max_reviews_per_pass) == (8, 4)
    assert section.max_attempts == 3


@pytest.mark.parametrize(
    "changes",
    [
        {"max_judge_chars": 0},
        {"max_judge_chars": MAX_JUDGE_CHARS_CEILING + 1},
        {"max_proposals_per_pass": 0},
        {"max_reviews_per_pass": 0},
        {"max_attempts": 0},
    ],
)
def test_lowering_section_rejects_out_of_range_values(changes: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        HoneyLoweringSection.model_validate(changes)
