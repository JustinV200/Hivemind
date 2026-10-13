"""Test RunnerReport: the JSON keys are exactly the six the Comb reads, and it is strict.

Fits into the Hive:
    Mirrors src/hivemind/royal_jelly/quarantine_comb/runner/report.py
    (codingrules section 3). Test infrastructure, not shipped.

Key invariants:
    - None: this module holds tests only.

See Also:
    - src/hivemind/royal_jelly/quarantine_comb/runner/report.py for the module under test.
"""

from __future__ import annotations

import json

import pytest
from builders.royal_jelly import make_runner_report
from pydantic import ValidationError

from hivemind.royal_jelly.quarantine_comb.runner.report import MAX_LIST_ITEMS, RunnerReport


def test_the_document_has_exactly_the_keys_the_comb_reads() -> None:
    document = json.loads(make_runner_report().model_dump_json(by_alias=True))

    assert set(document) == {"tests", "schema", "fuzz", "usage", "violations", "calls"}


def test_the_report_round_trips_through_json() -> None:
    report = make_runner_report(calls=[{"op": "exec", "target": "ls", "count": 2}])

    assert RunnerReport.model_validate_json(report.model_dump_json(by_alias=True)) == report


def test_unknown_fields_are_refused() -> None:
    document = json.loads(make_runner_report().model_dump_json(by_alias=True))
    document["rogue"] = 1

    with pytest.raises(ValidationError):
        RunnerReport.model_validate(document)


def test_lists_are_bounded() -> None:
    too_many = [{"op": "exec", "target": f"c{i}", "count": 1} for i in range(MAX_LIST_ITEMS + 1)]

    with pytest.raises(ValidationError):
        make_runner_report(calls=too_many)


def test_a_phase_that_never_finished_is_none() -> None:
    report = make_runner_report(tests=None, fuzz=None)

    assert report.tests is None
    assert report.fuzz is None
