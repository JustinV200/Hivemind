"""Chaos: an unknown client hammers the Entrance's invite route; the Reducer must fire.

Roadmap 13.6, over `hive serve`'s own composition with a remote listener (`e2e.remote_serve`). A
client that holds no invite and no enrolled key knocks on the remote listener's invite route with
codes that are no invite. Each knock is refused and recorded (`guard.entrance_redeem_failed`); past
the Guard Bee's `invite_abuse` threshold her next round reduces the Entrance: `guard.reduced` is on
the trail and the remote listener stops answering, while the operator's loopback listener still
serves. Once the Hive is down nothing is left open.

Fits into the Hive:
    Test infrastructure (codingrules section 14.2), not shipped.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.workers.roles.guard_bee rules.toml, `invite_abuse`, for the rule.
    - hivemind.entrance.reducer for what a reduction does.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import httpx
import pytest
from e2e.chaos.rig import assert_nothing_left_open, bounded
from e2e.entrance_stand import build_served, standing
from e2e.remote_serve import serve_exposed

from hivemind.cli.compose import Hive
from hivemind.cli.compose.entrance import ServedHive
from hivemind.entrance.enrol import REDEEM_FAILED_KIND
from hivemind.manifest import HiveManifest
from hivemind.pheromone import TrailQuery

pytestmark = [pytest.mark.e2e, pytest.mark.chaos]

_KNOCKS = 12  # Bad codes from one address: past the rule's threshold of five, under any limit.
_REDUCED_KIND = "guard.reduced"  # The Reducer's own record of OPEN -> REDUCED.
_OPTIONS = "/v1/enrol/passkey-options"  # The invite route a browser starts redeeming on.


def test_an_unknown_client_hammering_the_invite_route_reduces_the_entrance(
    tmp_path: Path,
) -> None:
    manifest, served = build_served(tmp_path)

    refused = asyncio.run(bounded("invite hammered", _hammered(manifest, served)))

    assert refused == _KNOCKS
    asyncio.run(assert_nothing_left_open(served.hive))


async def _hammered(manifest: HiveManifest, served: ServedHive) -> int:
    """Knock with bad codes on the remote listener, then wait for the Reducer to shut it."""
    async with standing(manifest, served, serve_exposed) as stand:
        remote_url = stand.remote_url  # Read while it serves: a reduced Entrance has none.
        refused = await _knock(remote_url)
        await stand.until(lambda: _recorded(served.hive, REDEEM_FAILED_KIND, _KNOCKS))
        await stand.until(lambda: _recorded(served.hive, _REDUCED_KIND, 1))
        await stand.until(lambda: stand.entrance.listeners.remote_port is None)
        await _assert_closed(remote_url)
        # The operator still has the Hive Stand: loopback serves after the reduction.
        assert (await stand.console.call(stand.session, "GET", "/v1/entrance/mode")).is_success
        return refused


async def _knock(remote_url: str) -> int:
    """Ask the invite route for options on `_KNOCKS` codes that are no invite; count refusals."""
    refused = 0
    async with httpx.AsyncClient(base_url=remote_url, timeout=10.0, trust_env=False) as stranger:
        for knock in range(_KNOCKS):
            answer = await stranger.post(_OPTIONS, json={"code": f"no-such-invite-{knock}"})
            refused += int(answer.is_client_error)
    return refused


async def _recorded(hive: Hive, kind: str, at_least: int) -> bool:
    """Whether the trail holds `at_least` events of `kind`."""
    return len(await hive.stores.trail.query(TrailQuery(kind=kind))) >= at_least


async def _assert_closed(remote_url: str) -> None:
    """The remote listener no longer answers the stranger, nor anyone else."""
    async with httpx.AsyncClient(timeout=2.0, trust_env=False) as client:
        with pytest.raises(httpx.TransportError):
            await client.post(f"{remote_url}{_OPTIONS}", json={"code": "again"})
