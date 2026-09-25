"""Tests for hivemind.cli.in_cell.hive_stand: the Hive Stand's names and addresses from a Cell.

Roadmap step 10.3a (gap 5): the Queen URL's host is resolved once at start, so the Hive-state
floor knows every address the Hive Stand answers this Cell on; a name that does not resolve is
logged and leaves no address, never an error; an onion service is never looked up at all.

Fits into the Hive:
    Mirrors src/hivemind/cli/in_cell/hive_stand.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only. Every lookup goes through a `FakeResolver`.

See Also:
    - hivemind.cli.in_cell.hive_stand for the module under test.
"""

from __future__ import annotations

import dataclasses
import ipaddress
from typing import cast

import pytest
from structlog.testing import capture_logs

from hivemind.cli.in_cell.config import InCellRuntimeConfig
from hivemind.cli.in_cell.hive_stand import hive_stand_addresses, hive_stand_names
from hivemind.guard.net import FakeResolver

_ONION = "7jjm54ntxrtbp4fjhhw2gdk7zz2fshgnubimtmc5dcczncvdfo3lnbid.onion"  # A valid v3 address.


@dataclasses.dataclass(frozen=True)
class _Config:
    """The one field these functions read, standing in for a whole InCellRuntimeConfig."""

    queen_waggle_url: str


def _config(url: str) -> InCellRuntimeConfig:
    return cast(InCellRuntimeConfig, _Config(url))


async def test_a_gateway_name_is_resolved_once_on_its_port() -> None:
    resolver = FakeResolver({"host.docker.internal": ["172.17.0.1"]})

    addresses = await hive_stand_addresses(_config("ws://host.docker.internal:8710"), resolver)

    assert addresses == (ipaddress.ip_address("172.17.0.1"),)
    assert resolver.lookups == [("host.docker.internal", 8710)]


async def test_an_address_is_its_own_answer_and_never_looked_up() -> None:
    resolver = FakeResolver()

    addresses = await hive_stand_addresses(_config("ws://10.0.2.2:8710"), resolver)

    assert addresses == (ipaddress.ip_address("10.0.2.2"),)
    assert resolver.lookups == []


async def test_an_onion_service_is_never_resolved_here() -> None:
    resolver = FakeResolver()

    addresses = await hive_stand_addresses(_config(f"ws://{_ONION}:8710"), resolver)

    assert addresses == ()
    assert resolver.lookups == []


async def test_a_name_that_does_not_resolve_is_a_warning_not_an_error() -> None:
    with capture_logs() as logs:
        addresses = await hive_stand_addresses(
            _config("ws://host.docker.internal"), FakeResolver(default=None)
        )

    assert addresses == ()
    [warning] = [log for log in logs if log["event"] == "cell.hive_stand.unresolved"]
    assert warning["log_level"] == "warning"
    assert warning["host"] == "host.docker.internal"


@pytest.mark.parametrize(
    ("url", "names"),
    [
        ("ws://host.docker.internal:8710", ("host.docker.internal",)),
        (f"ws://{_ONION}:8710", (_ONION,)),
        ("ws://10.0.2.2:8710", ()),
        ("ws://[::1]:8710", ()),
    ],
)
def test_the_host_is_a_name_the_floor_refuses_unless_it_is_an_address(
    url: str, names: tuple[str, ...]
) -> None:
    assert hive_stand_names(_config(url)) == names
