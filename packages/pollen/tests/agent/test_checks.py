"""Test the capability matcher: the second, independent wall behind the Hive Stand's own check.

The Queen (the Hive's central orchestrator) grants a device capability strings when it enrols, and
the Pollen Packet (the thin gateway program on a device the Hive borrows) checks every request
against them before any executor runs (gateway-contract section 10). A string is ``family:scope``
in one of five families; any other family, or a string that does not parse, grants nothing. For the
four path-like families the held scope is a glob (equal as text, or accepted by ``fnmatch`` against
the needed scope, case-sensitively, with backslashes read as slashes in both); for ``watch`` it is
a prefix. The literal ``{scratch}`` in a held scope stands for the lease's scratch root, with the
glob metacharacters in the root made literal. The matcher is pure, so it is tested with examples
straight from the contract.

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Tests pollen.agent.checks.

Key invariants:
    - An unknown or unparsable capability never widens anything.
    - A denial's message names the family and the request, never the scope (a scope is a path).

See Also:
    - docs/waggle/gateway-contract.md section 10 and docs/waggle/enrolment.md section 7.1.
"""

from __future__ import annotations

import pytest

from pollen.agent import check_needs, covers, parse_capability
from pollen.agent.checks import substitute_scratch
from pollen.errors import CapabilityDeniedError
from pollen.executors import Need


@pytest.mark.parametrize(
    ("text", "parsed"),
    [
        ("exec:*", ("exec", "*")),
        ("fs:read:/home/**", ("fs:read", "/home/**")),
        ("fs:write:{scratch}/**", ("fs:write", "{scratch}/**")),
        ("cell:outside_scratch:**", ("cell:outside_scratch", "**")),
        ("watch:cell_x", ("watch", "cell_x")),
        ("fs:read:", ("fs:read", "")),
        ("net:host:example.org", None),
        ("exec", None),
        ("", None),
        ("fs:delete:/x", None),
    ],
)
def test_a_capability_parses_into_its_family_and_scope_or_into_nothing(
    text: str, parsed: tuple[str, str] | None
) -> None:
    assert parse_capability(text) == parsed


@pytest.mark.parametrize(
    ("root", "expected"),
    [
        ("/data/leases/one", "/data/leases/one"),
        ("/data/leases/one/", "/data/leases/one"),
        ("C:\\Users\\me\\leases\\one", "C:/Users/me/leases/one"),
        ("/odd[name]/x*y?z", "/odd[[]name]/x[*]y[?]z"),
    ],
)
def test_the_scratch_token_is_replaced_by_the_escaped_root(root: str, expected: str) -> None:
    assert substitute_scratch("{scratch}/**", root) == f"{expected}/**"


def test_the_scratch_token_is_left_alone_with_no_root_or_no_token() -> None:
    assert substitute_scratch("{scratch}/**", None) == "{scratch}/**"
    assert substitute_scratch("/plain/**", "/data") == "/plain/**"


def _covered(held: list[str], family: str, scope: str, root: str | None = None) -> bool:
    """Say whether ``held`` covers one need."""
    return covers(held, Need(family, scope), root)


@pytest.mark.parametrize(
    ("held", "family", "scope", "expected"),
    [
        (["exec:*"], "exec", "/usr/bin/python3", True),
        (["exec:python3"], "exec", "python3", True),
        (["exec:python3"], "exec", "python", False),
        (["exec:py*"], "exec", "python3", True),
        (["exec:Python3"], "exec", "python3", False),  # Case-sensitive.
        (["fs:read:/home/**"], "fs:read", "/home/me/deep/file.txt", True),
        (["fs:read:/home/**"], "fs:read", "/home", False),
        (["fs:read:/home/m?"], "fs:read", "/home/me", True),
        (["fs:read:/home/[ab]c"], "fs:read", "/home/bc", True),
        (["fs:read:/home/[!ab]c"], "fs:read", "/home/bc", False),
        (["fs:read:C:\\Users\\**"], "fs:read", "C:/Users/me/x", True),  # Backslash is a slash.
        (["fs:read:C:/Users/**"], "fs:read", "C:\\Users\\me\\x", True),
        (["fs:read:**"], "fs:write", "/x", False),  # Another family.
        (["net:host:*", "garbage"], "exec", "x", False),  # Unknown strings grant nothing.
        ([], "exec", "x", False),
    ],
)
def test_a_held_scope_covers_by_text_or_by_glob_in_its_own_family(
    held: list[str], family: str, scope: str, expected: bool
) -> None:
    assert _covered(held, family, scope) is expected


@pytest.mark.parametrize(
    ("held", "scope", "expected"),
    [
        (["watch:cell_x"], "cell_x", True),
        (["watch:cell_*"], "cell_x", True),
        (["watch:cell_x"], "cell_xy", False),
        (["watch:cell_x*"], "cell_xy", True),
        (["watch:*_x"], "cell_x", False),  # A prefix, not a glob: only a trailing star counts.
        (["watch:cell_y"], "cell_x", False),
    ],
)
def test_a_watch_scope_is_a_prefix(held: list[str], scope: str, expected: bool) -> None:
    assert _covered(held, "watch", scope) is expected


def test_the_scratch_grant_covers_only_its_own_lease_root() -> None:
    held = ["fs:write:{scratch}/**"]
    assert _covered(held, "fs:write", "/data/leases/one/sub/file", "/data/leases/one")
    assert not _covered(held, "fs:write", "/data/leases/one-other/file", "/data/leases/one")
    assert not _covered(held, "fs:write", "/etc/passwd", "/data/leases/one")
    assert not _covered(held, "fs:write", "/data/leases/one/file", None)


def test_a_root_with_glob_characters_matches_only_itself() -> None:
    held = ["fs:write:{scratch}/**"]
    root = "/data/leases/a*b"
    assert _covered(held, "fs:write", "/data/leases/a*b/file", root)
    assert not _covered(held, "fs:write", "/data/leases/aXXb/file", root)


def test_every_need_must_be_covered() -> None:
    needs = (Need("exec", "/bin/sh"), Need("fs:write", "/data/x"))
    check_needs(["exec:*", "fs:write:/data/*"], needs, None, "msg_1")
    with pytest.raises(CapabilityDeniedError) as raised:
        check_needs(["exec:*"], needs, None, "msg_1")
    assert "fs:write" in str(raised.value) and "msg_1" in str(raised.value)
    assert "/data/x" not in str(raised.value)


def test_a_request_with_no_needs_is_always_admitted() -> None:
    check_needs([], (), None, "msg_1")
