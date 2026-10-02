"""Test that a label lowering proposal's reasons leave the Entrance only behind C2 clearance.

Phase 7 handoff item 3: a clearance judge once copied an API key into its verdict reason, so the
reasons on a label lowering proposal (ADR-0038) are C2 content wherever they are shown. Today the
Entrance's Honey browser serves folders, items and search but no proposal, and no stream view
serves one either; this test pins that whatever route or view starts serving a proposal has to put
it behind ``honey:clearance:c2`` before any reason can leave the Hive Stand.

Fits into the Hive:
    Mirrors src/hivemind/entrance/routes/hive/honey.py's proposal invariant (codingrules section
    3). Reads the real route table the listeners are built from
    (``hivemind.entrance.app.route_table``).

Key invariants:
    - None: this module holds tests only.
"""

from __future__ import annotations

import json
from collections.abc import Iterator

from pydantic import BaseModel

from hivemind.entrance.app import route_table
from hivemind.entrance.gate.spec import Access

# Every free-text reason a LoweringProposal carries; any of them may quote the deposit's text.
_REASON_FIELDS = ("ripener_reason", "verdict_reasons", "human_reason")


def _served_models() -> Iterator[tuple[str, Access, type[BaseModel]]]:
    """Yield every body the Entrance serves: each route's response and each view's frames."""
    table = route_table()
    for route in table.routes:
        if route.response_model is not None:
            yield route.path, route.access, route.response_model
    for socket in table.sockets:
        for model in (socket.frame_model, *socket.reply_frames):
            yield socket.path, socket.access, model


def test_no_route_or_view_serves_a_proposals_reasons_without_c2_clearance() -> None:
    exposed = [
        path
        for path, access, model in _served_models()
        if any(field in json.dumps(model.model_json_schema()) for field in _REASON_FIELDS)
        and not access.c2
    ]

    assert exposed == []
