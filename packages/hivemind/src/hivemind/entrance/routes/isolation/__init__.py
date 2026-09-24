"""The Cell isolation resource: the human's two levers on a Cell (roadmap step 10.6a, ADR-0035).

``POST /v1/cells/{cell_id}/isolate`` and ``POST /v1/cells/{cell_id}/lift``, each for an
interactive device inside its step-up window holding ``entrance:steward``, each through the
Queen's door (`cells`), with their bodies and views (`views`). The Queen isolates any Cell but the
Hive Stand's own lease herself; the human alone isolates that one and lifts any isolation.

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
"""

from hivemind.entrance.routes.isolation.cells import ROUTES, STEWARD
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
    "CellIsolationView",
    "CellLiftView",
    "IsolateBody",
    "isolation_view",
    "lift_view",
]
