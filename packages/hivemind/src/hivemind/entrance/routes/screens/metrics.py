"""Serve the metrics resource: the Hive's figures as Prometheus text exposition.

``GET /v1/metrics`` answers the metrics snapshot in the text exposition format 0.0.4 a Prometheus
server scrapes, so the figures the screens show can be graphed and alerted on from outside. It is
a normal Landing Board route: an enrolled device (a program with the ``observe`` capability) scrapes
it with its signed session like any other client, and the document declares its media type. Every
metric is a count or a figure; none carries a word.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.routes.screens``.
    Registered in the route table. Calls into ``hivemind.entrance.reads.screens`` and the metrics
    renderer in the observation face.

Key invariants:
    - Read only; the body's media type is ``text/plain; version=0.0.4; charset=utf-8``.

See Also:
    - hivemind.observation.metrics.exposition for the text format.
"""

from __future__ import annotations

from starlette.responses import PlainTextResponse

from hivemind.entrance.gate.params import Services
from hivemind.entrance.gate.spec import BOTH_LISTENERS, RouteEffect, RouteSpec, session_with
from hivemind.entrance.reads.screens import read_metrics
from hivemind.observation import METRICS_CONTENT_TYPE, render_snapshot

OBSERVE = "observe"  # The metrics are figures only.

__all__ = ["ROUTES", "PrometheusResponse"]


class PrometheusResponse(PlainTextResponse):
    """A plain-text body served as the exposition format a Prometheus scraper expects."""

    media_type = METRICS_CONTENT_TYPE


async def get_metrics(services: Services) -> PrometheusResponse:
    """Answer the metrics in Prometheus text exposition format.

    Args:
        services: The Entrance's services.

    Returns:
        The exposition text.
    """
    # Latency: the census's reads plus indexed store reads; a scrape every few seconds is fine.
    snapshot = await read_metrics(services.hive, services.clock.now())
    return PrometheusResponse(render_snapshot(snapshot))


ROUTES: tuple[RouteSpec, ...] = (
    RouteSpec(
        method="GET",
        path="/v1/metrics",
        listeners=BOTH_LISTENERS,
        access=session_with(OBSERVE),
        effect=RouteEffect.READ,
        endpoint=get_metrics,
        summary="Read the Hive's metrics as Prometheus text exposition (version 0.0.4).",
        response_class=PrometheusResponse,
    ),
)
