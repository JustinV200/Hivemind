"""Test hivemind.entrance.help.errors: each refusal has its own code and status.

Fits into the Hive:
    Mirrors src/hivemind/entrance/help/errors.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.
"""

from __future__ import annotations

import pytest

from hivemind.common.errors import HiveMindError
from hivemind.entrance.errors import EntranceError
from hivemind.entrance.gate import status_for
from hivemind.entrance.help import errors
from hivemind.entrance.help.errors import HelpNotServedError, QuestionOverBudgetError


@pytest.mark.parametrize(
    ("error", "status"), [(HelpNotServedError(), 404), (QuestionOverBudgetError(), 429)]
)
def test_each_refusal_is_an_entrance_error_with_its_own_code_and_status(
    error: HiveMindError, status: int
) -> None:
    classes = [getattr(errors, name) for name in errors.__all__]

    assert isinstance(error, EntranceError)
    assert status_for(error) == status
    assert error.code.startswith("hivemind.entrance.help_")
    assert len({cls.code for cls in classes}) == len(classes)
