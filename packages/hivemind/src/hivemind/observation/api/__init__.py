"""Hold the Observation Hive's aggregated screen models and the pure builders that compose them.

Codingrules 8.11: every Observation Hive screen is one data-shaped model the web app renders. The
single-resource models live in ``hivemind.observation.views``; the screens that join several
resources live here, one module per screen: the summary strip (``summary``), the Fleet list
(``fleet``), a Cell page (``cell_page``), an Attendant view (``attendant``), the Thoughts page
(``thoughts``), the Capping view (``capping``) and the Cost view (``cost``), with the shared pieces
they fold (open Alarms in ``alarms``, a Cell's model hosting in ``hosting``). Each module holds its
read model and a pure builder over records the Entrance gathers, so a screen is regenerated from
its model alone and the builders are tested without a store.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.observation``. Used by
    ``hivemind.entrance.reads.screens`` (which gathers the records) and the screens' routes,
    through ``hivemind.observation``'s face; never imports the Entrance. Calls into the Hive's
    record types, the sibling views and pydantic.

Key invariants:
    - This file holds re-exports and ``__all__`` only.
    - Every builder is pure (codingrules 8.3): nothing here reads or writes a store.
    - A change to a model here is a contract change: additive within ``/v1/`` (ADR-0042).

See Also:
    - .claude/roadmap.md step 12.1 for the screens these models feed.

Public API:
    - SummaryView, SummaryInputs, summary_view: the summary strip (summary).
    - FleetView, FleetRow, FleetInputs, fleet_view, TIER_LEGEND: the Fleet list (fleet).
    - CellPageView, CellPageInputs, cell_page: a Cell page (cell_page).
    - AttendantView, AttendantInputs, attendant_view: an Attendant view (attendant).
    - ThoughtsPage, ThoughtsQuery, thoughts_page: the Thoughts page (thoughts).
    - CappingView, capping_view: the Capping view (capping).
    - CostView, cost_view, cost_lines: the Cost view (cost).
    - OpenAlarmView, open_alarms: open Alarms folded from the trail (alarms).
    - HostingFacts, HostingView, hosting_facts, hosting_view, models_from: model hosting (hosting).
"""

from hivemind.observation.api.alarms import ALARM_FAMILY, OpenAlarmView, alarms_on_cell, open_alarms
from hivemind.observation.api.attendant import (
    HUMAN_PRINCIPAL,
    QUEEN_PRINCIPAL,
    RECENT_FINISHED,
    UP_NEXT,
    AttendantInputs,
    AttendantView,
    InboxEntryView,
    TaskEdge,
    TaskNode,
    attendant_view,
    reconstruct_inbox,
)
from hivemind.observation.api.capping import (
    MAX_ROWS,
    AuditRow,
    CappingView,
    ProposalRow,
    TierAuditRate,
    TierStateCount,
    VerdictRow,
    capping_view,
    cappings_on_cell,
)
from hivemind.observation.api.cell_page import (
    WARDEN_ROLE,
    BeeRow,
    CellPageInputs,
    CellPageView,
    PendingRequestView,
    WardenForageView,
    WaxEntryView,
    cell_page,
    pending_requests,
)
from hivemind.observation.api.cost import (
    CALL_KIND,
    CostLine,
    CostView,
    GoalCost,
    ProviderCost,
    SlotSaving,
    cost_lines,
    cost_view,
)
from hivemind.observation.api.fleet import (
    TIER_LEGEND,
    UNKNOWN_STATUS,
    FleetInputs,
    FleetLinks,
    FleetRow,
    FleetView,
    TierLegendEntry,
    WardenRuns,
    cell_status,
    fleet_view,
    highest_severity,
    live_wax,
    warden_runs,
)
from hivemind.observation.api.hosting import (
    HostingFacts,
    HostingView,
    ModelsFrom,
    SlotHostingView,
    hosting_facts,
    hosting_view,
    models_from,
)
from hivemind.observation.api.summary import (
    ACTIVITY_WINDOW_S,
    SummaryInputs,
    SummaryView,
    summary_view,
)
from hivemind.observation.api.thoughts import (
    DEFAULT_THOUGHTS_PAGE,
    MAX_DECISIONS,
    MAX_THOUGHTS_PAGE,
    AutopilotDecisionView,
    ThoughtsPage,
    ThoughtsQuery,
    decode_cursor,
    encode_cursor,
    resolve_principal,
    thoughts_page,
)

__all__ = [
    "ACTIVITY_WINDOW_S",
    "ALARM_FAMILY",
    "CALL_KIND",
    "DEFAULT_THOUGHTS_PAGE",
    "HUMAN_PRINCIPAL",
    "MAX_DECISIONS",
    "MAX_ROWS",
    "MAX_THOUGHTS_PAGE",
    "QUEEN_PRINCIPAL",
    "RECENT_FINISHED",
    "TIER_LEGEND",
    "UNKNOWN_STATUS",
    "UP_NEXT",
    "WARDEN_ROLE",
    "AttendantInputs",
    "AttendantView",
    "AuditRow",
    "AutopilotDecisionView",
    "BeeRow",
    "CappingView",
    "CellPageInputs",
    "CellPageView",
    "CostLine",
    "CostView",
    "FleetInputs",
    "FleetLinks",
    "FleetRow",
    "FleetView",
    "GoalCost",
    "HostingFacts",
    "HostingView",
    "InboxEntryView",
    "ModelsFrom",
    "OpenAlarmView",
    "PendingRequestView",
    "ProposalRow",
    "ProviderCost",
    "SlotHostingView",
    "SlotSaving",
    "SummaryInputs",
    "SummaryView",
    "TaskEdge",
    "TaskNode",
    "ThoughtsPage",
    "ThoughtsQuery",
    "TierAuditRate",
    "TierLegendEntry",
    "TierStateCount",
    "VerdictRow",
    "WardenForageView",
    "WardenRuns",
    "WaxEntryView",
    "alarms_on_cell",
    "attendant_view",
    "capping_view",
    "cappings_on_cell",
    "cell_page",
    "cell_status",
    "cost_lines",
    "cost_view",
    "decode_cursor",
    "encode_cursor",
    "fleet_view",
    "highest_severity",
    "hosting_facts",
    "hosting_view",
    "live_wax",
    "models_from",
    "open_alarms",
    "pending_requests",
    "reconstruct_inbox",
    "resolve_principal",
    "summary_view",
    "thoughts_page",
    "warden_runs",
]
