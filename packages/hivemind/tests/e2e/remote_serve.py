"""Serve `hive serve`'s Hive with a remote listener too, under the serving rig's test-only vpn plan.

A phone reaches the Hive Entrance (the Hive's one HTTP door) through its remote listener, which
`hive serve` opens only in a remote mode, on an overlay (VPN) address with TLS on a DNS name
(ADR-0033): `plan_exposure` refuses anything less, rightly, and a test host has no overlay interface
to bind. `serve_exposed` runs `serve_hive` itself over the Hive `build_served_hive` built (a real
Queen, the orchestrator; her Warden, supervising the Hive Stand, the machine she runs on; a Drone,
the worker bee doing the task; the Hive's own SQLite file and secret store; the push transport and
resolver its `ServedHive` carries), handing it the plan the builders' serving rig uses through
`ServedHive.plan` instead of one planned from the manifest: `vpn` mode with its public origin and
relying party (`hive.example.ts.net`), the remote listener on a second loopback port, plain HTTP.
Everything else is `hive serve`'s own composition, so this cannot drift from it: the remote
application has no loopback-only route, its own passkey relying party and origin, and sessions
opened on it work nowhere else.

Fits into the Hive:
    Test infrastructure (codingrules section 14.5), not shipped. Handed to
    `e2e.entrance_stand.standing` by the phone test of phase 10's third exit criterion.

Key invariants:
    - The plan differs from a real vpn plan only in where the remote listener binds and its TLS.

See Also:
    - hivemind.cli.compose.entrance for `serve_hive` and the `ServedHive.plan` seam.
    - builders.entrance.serving for the rig whose plan, origin and relying party this reuses.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import replace

from builders.entrance.serving import PUBLIC_ORIGIN, REMOTE_RP_ID

from hivemind.cli.compose.entrance import ServedHive, serve_hive
from hivemind.entrance.expose import ExposurePlan, ListenerPlan
from hivemind.entrance.runtime import HiveEntrance
from hivemind.manifest import EntranceExposure

LOOPBACK_HOST = "127.0.0.1"  # Where both listeners bind: the remote one stands in for the overlay.
# Both listeners on loopback ports the system picks; no TLS, which only this test plan may lack.
_LISTENER = ListenerPlan(LOOPBACK_HOST, 0, None)
_RIG_PLAN = ExposurePlan(
    mode=EntranceExposure.VPN,
    loopback=_LISTENER,
    remote=_LISTENER,
    public_origin=PUBLIC_ORIGIN,
    rp_id=REMOTE_RP_ID,
    tunnel_argv=(),
)

__all__ = ["LOOPBACK_HOST", "serve_exposed"]


@asynccontextmanager
async def serve_exposed(served: ServedHive) -> AsyncIterator[HiveEntrance]:
    """Run `serve_hive` over ``served`` under the rig's vpn plan, both listeners serving.

    Args:
        served: The Hive `build_served_hive` built, not yet started.

    Yields:
        The running Entrance: `listeners.remote_port` is the phone's way in.
    """
    async with serve_hive(replace(served, plan=_RIG_PLAN)) as entrance:
        yield entrance
