"""Tests for hivemind.honey_store.browse.errors: the browser's error tree and its codes.

Fits into the Hive:
    Mirrors src/hivemind/honey_store/browse/errors.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.honey_store.browse.errors for the module under test.
"""

from __future__ import annotations

from hivemind.honey_store.browse.errors import (
    BrowseError,
    BrowseInputError,
    BrowseNotFoundError,
    BrowsePathError,
)
from hivemind.honey_store.errors import HoneyStoreError


def test_every_browse_error_is_a_honey_store_error_with_its_own_code() -> None:
    errors = (
        BrowsePathError("/x", "why"),
        BrowseNotFoundError("/hive/x"),
        BrowseInputError("title", "it is empty"),
    )

    assert all(isinstance(error, BrowseError | HoneyStoreError) for error in errors)
    assert len({error.code for error in errors} | {BrowseError.code}) == len(errors) + 1


def test_browse_errors_name_the_path_or_field_they_are_about() -> None:
    path_error = BrowsePathError("/nope", "there is no such folder")
    not_found = BrowseNotFoundError("/hive/honey_x")
    input_error = BrowseInputError("reason", "a relabel must say why")

    assert (path_error.path, path_error.why) == ("/nope", "there is no such folder")
    assert "/nope" in str(path_error)
    assert not_found.path == "/hive/honey_x" and "/hive/honey_x" in str(not_found)
    assert input_error.field == "reason" and "must say why" in str(input_error)
