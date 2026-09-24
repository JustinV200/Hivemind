"""Pin protocol 1.6's ScoutReport: what a Scout found, carried on task.result and task.assign.

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Exercises waggle.messages.task.recon against spec
    section 8.2: construction, round trip, every bound, and the two messages that carry it.

Key invariants:
    - None: this module holds tests only.

See Also:
    - docs/waggle/spec.md section 8.2 for ScoutReport.
    - .claude/roadmap.md step 6.10 for the Scout role.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from waggle.messages.task import ScoutReport
from waggle.messages.task.recon import (
    MAX_RECON_FINDINGS,
    MAX_RECON_ITEM_CHARS,
    MAX_RECON_RISKS,
    MAX_RECON_SUMMARY_CHARS,
)


def _report(**overrides: object) -> ScoutReport:
    """A valid ScoutReport, then `overrides` applied."""
    fields: dict[str, object] = {
        "feasible": True,
        "summary": "The login form is at /login and takes a username and a password.",
        "findings": ("Two text inputs and one button.",),
        "suggested_steps": ("Fill the username.", "Fill the password.", "Click Log in."),
        "risks": ("A wrong password locks the account after three tries.",),
        "targets": ("http://127.0.0.1:8080/login",),
    }
    return ScoutReport.model_validate({**fields, **overrides})


def test_scout_report_round_trips() -> None:
    report = _report()

    assert ScoutReport.model_validate_json(report.model_dump_json()) == report


def test_only_feasible_and_summary_are_required() -> None:
    report = ScoutReport(feasible=False, summary="Nothing to log in to.")

    assert report.findings == ()
    assert report.targets == ()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("summary", ""),
        ("summary", "x" * (MAX_RECON_SUMMARY_CHARS + 1)),
        ("findings", ("x" * (MAX_RECON_ITEM_CHARS + 1),)),
        ("findings", ("",)),
        ("findings", ("f",) * (MAX_RECON_FINDINGS + 1)),
        ("risks", ("r",) * (MAX_RECON_RISKS + 1)),
    ],
)
def test_every_bound_is_enforced(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        _report(**{field: value})


def test_scout_report_forbids_extra_fields() -> None:
    with pytest.raises(ValidationError):
        ScoutReport.model_validate({"feasible": True, "summary": "s", "verdict": "go"})
