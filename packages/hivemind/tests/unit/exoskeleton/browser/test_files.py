"""Unit tests for hivemind.exoskeleton.browser.files: the refusal every browser gives a file URL.

Fits into the Hive:
    Mirrors src/hivemind/exoskeleton/browser/files.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.exoskeleton.browser.files for the module under test.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hivemind.exoskeleton.browser.files import OUTSIDE_FILE_ROOTS, refuse_outside_roots
from hivemind.exoskeleton.errors import PeripheralError

_ROOTS = (Path("/srv/lease/scratch"),)


def test_a_file_url_outside_the_roots_is_refused_without_naming_it() -> None:
    with pytest.raises(PeripheralError) as refused:
        refuse_outside_roots("file:///home/bee/.ssh/id_rsa", _ROOTS, "navigate")

    assert refused.value.reason == OUTSIDE_FILE_ROOTS
    assert refused.value.operation == "navigate"
    assert "id_rsa" not in str(refused.value)


@pytest.mark.parametrize(
    "url", ["file:///srv/lease/scratch/page.html", "https://example.test/", "about:blank"]
)
def test_anything_else_passes_quietly(url: str) -> None:
    refuse_outside_roots(url, _ROOTS, "navigate")
