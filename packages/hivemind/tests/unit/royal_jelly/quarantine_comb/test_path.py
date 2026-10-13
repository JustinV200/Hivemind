"""Test decide_path: the sandbox route or the real-cell route for a target platform.

Fits into the Hive:
    Mirrors src/hivemind/royal_jelly/quarantine_comb/path.py
    (codingrules section 3). Test infrastructure, not shipped.

Key invariants:
    - None: this module holds tests only.

See Also:
    - src/hivemind/royal_jelly/quarantine_comb/path.py for the module under test.
"""

from __future__ import annotations

import pytest

from hivemind.royal_jelly.quarantine_comb import decide_path
from hivemind.royal_jelly.quarantine_comb.path import PATH_RATIONALE
from waggle.messages.labels import OsFamily
from waggle.messages.reports import PlatformReport
from waggle.messages.tool.authoring import QuarantinePath


def _platform(os: OsFamily, distribution: str | None) -> PlatformReport:
    """Build a platform report with only the fields the decision reads."""
    return PlatformReport(  # noqa: S604  # `shell` is the login-shell field, not subprocess.
        os=os,
        distribution=distribution,
        architecture="x86_64",
        package_manager=None,
        shell="/bin/sh",
        python_version=None,
    )


@pytest.mark.parametrize(
    ("os", "distribution", "sandbox", "expected"),
    [
        (OsFamily.LINUX, "ubuntu", "ubuntu", QuarantinePath.SANDBOX_CELL),
        (OsFamily.LINUX, "Ubuntu", "UBUNTU", QuarantinePath.SANDBOX_CELL),
        (OsFamily.LINUX, "Ubuntu 24.04.1 LTS", "ubuntu", QuarantinePath.SANDBOX_CELL),
        (OsFamily.LINUX, "Debian GNU/Linux 12", "ubuntu", QuarantinePath.REAL_CELL),
        (OsFamily.LINUX, "Ubuntustudio 24", "ubuntu", QuarantinePath.REAL_CELL),
        (OsFamily.LINUX, None, "ubuntu", QuarantinePath.REAL_CELL),
        (OsFamily.LINUX, "Ubuntu 24.04", "", QuarantinePath.REAL_CELL),
        (OsFamily.WINDOWS, None, "ubuntu", QuarantinePath.REAL_CELL),
        (OsFamily.WINDOWS, "ubuntu", "ubuntu", QuarantinePath.REAL_CELL),
        (OsFamily.MACOS, "macOS 14.5", "ubuntu", QuarantinePath.REAL_CELL),
    ],
)
def test_decide_path_chooses_the_sandbox_only_for_the_sandbox_image_platform(
    os: OsFamily, distribution: str | None, sandbox: str, expected: QuarantinePath
) -> None:
    assert decide_path(_platform(os, distribution), sandbox) is expected


@pytest.mark.parametrize(
    ("os", "sandbox_os", "expected"),
    [
        (OsFamily.WINDOWS, OsFamily.WINDOWS, QuarantinePath.SANDBOX_CELL),
        (OsFamily.MACOS, OsFamily.MACOS, QuarantinePath.SANDBOX_CELL),
        (OsFamily.LINUX, OsFamily.WINDOWS, QuarantinePath.REAL_CELL),
        (OsFamily.WINDOWS, OsFamily.MACOS, QuarantinePath.REAL_CELL),
    ],
)
def test_a_non_linux_sandbox_takes_its_own_family_and_nothing_else(
    os: OsFamily, sandbox_os: OsFamily, expected: QuarantinePath
) -> None:
    # Only the development subprocess sandbox is ever non-Linux: it is the host, so the family
    # is the whole platform and no distribution label is compared.
    assert decide_path(_platform(os, None), "ubuntu", sandbox_os) is expected


def test_every_route_has_a_rationale() -> None:
    assert set(PATH_RATIONALE) == set(QuarantinePath)
