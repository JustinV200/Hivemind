"""The Cell isolation resource: the human's levers on a Cell (roadmap steps 10.6a and 13.4a).

``POST /v1/cells/{cell_id}/isolate`` and ``POST /v1/cells/{cell_id}/lift``, each for an interactive
device inside its step-up window holding ``entrance:steward``, each through the Queen's door
(`cells`), with their bodies and views (`views`); and ``POST /v1/cells/{target}/sting-cut``, which
also needs the typed break-glass phrase (`sting_cut`). The Queen isolates any Cell but the Hive
Stand's own lease herself; the human alone isolates that one and lifts any isolation.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.routes``. Its rows join
    the route table in ``hivemind.entrance.routes.registry``. Calls into the step-up gate, the
    Queen's door and ``hivemind.queen.isolation``'s outcomes only.

Key invariants:
    - Neither lever runs without an interactive, stepped-up session.

See Also:
    - hivemind.queen.isolation for the one path and the lift.

Public API (roadmap step 10.6a):
    - ROUTES, STEWARD: the two rows and the capability they need (cells).
    - IsolateBody, CellIsolationView, CellLiftView, isolation_view, lift_view: the bodies (views).
    - STING_CUT_ROUTES, StingCutBody, StingCutView, sting_cut_view: the Sting Cut (sting_cut,
      roadmap step 13.4a).
"""

from hivemind.entrance.routes.isolation.cells import ROUTES, STEWARD
from hivemind.entrance.routes.isolation.sting_cut import ROUTES as STING_CUT_ROUTES
from hivemind.entrance.routes.isolation.sting_cut import StingCutBody, StingCutView, sting_cut_view
from hivemind.entrance.routes.isolation.views import (
    CellIsolationView,
    CellLiftView,
    IsolateBody,
    isolation_view,
    lift_view,
)

__all__ = [
    "ROUTES",
    "STEWARD",
    "STING_CUT_ROUTES",
    "CellIsolationView",
    "CellLiftView",
    "IsolateBody",
    "StingCutBody",
    "StingCutView",
    "isolation_view",
    "lift_view",
    "sting_cut_view",
]
