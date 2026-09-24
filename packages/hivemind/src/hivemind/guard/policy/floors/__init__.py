"""The Guard's floors: what no principal may do, whatever its capability set holds (ADR-0031).

`hivemind.guard.policy.evaluate` runs the floors before every other rule, and only ever to
refuse. Two families of floor live here. The Hive-state floor (`hive_state`, ADR-0033) refuses
every bee (a Warden or a Worker) the Hive's own state: its database, secret store and manifest
paths, its `hive` and `hivemind-*` entry points, and every loopback name or address, the
unspecified and link-local addresses and the Hive Stand's own addresses, judged on what a host
resolved to as well as how it is written. The tier floors guard Night Veil (roadmap steps
10.3a-10.3d): its work is initiated only by a human's own goal request naming the tier
(`initiation`); it runs only on a fresh Virtual Cell, binds only local models, touches Honey at
c0 and c1 only, is location-blind, and its Cell dials the Queen only over a Tor hidden service
(`night_veil`); and a task is never given a weaker tier's egress than the one it is bound to
(`inheritance`). `chain` runs them in one order; `refusal` is the shape of every answer. Every
floor reads `PolicyRequest.context` and policy data, never the shape of the held set, so a floor
holds whatever a set says.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy), inside `hivemind.guard.policy`.
    Called by `hivemind.guard.policy.evaluate`. Calls into `hivemind.cell`,
    `hivemind.guard.capabilities`, `hivemind.guard.net` and the policy package's `facts`,
    `hive_state`, `models`, `points` and `table`.

Key invariants:
    - Pure: no floor performs I/O, reads a clock or writes the trail.
    - Every floor only refuses; the held set is the one rule that can allow.

See Also:
    - docs/adr/0031-capability-model-attenuation-and-enforcement-points.md, "Floors hold whatever
      a set says".
    - docs/adr/0033-landing-board-enrolment-two-factor-login-and-exposure.md, "Bees never touch
      the Hive's own state".
    - docs/adr/0030-night-veil-retention-and-clearance-boundary.md for Night Veil's boundary.

Public API:
    - FloorRefusal, Floor, TIER_FLOOR_RULE, STATE_FLOOR_RULE: a floor's answer, its signature and
      the two rule-id families (refusal).
    - FLOORS, floor_refusal: the ordered floors and the one call that runs them (chain).
    - hive_state_floor and its floor names (STATE_PATHS_FLOOR, ENTRY_POINTS_FLOOR,
      LOOPBACK_FLOOR, ENTRY_POINT, ENTRY_POINT_PREFIX): the Hive-state floor (hive_state).
    - night_veil_floor, is_night_veil and its floor names (VIRTUAL_ONLY_FLOOR, LOCAL_SLOTS_FLOOR,
      CLEARANCE_FLOOR, LOCATION_FLOOR, CONTROL_LINK_FLOOR, LOCATION_FAMILIES, ONION_SUFFIX,
      REMOTE_DNS_SOCKS_SCHEMES): the Night Veil floors (night_veil).
    - initiation_floor, INITIATION_FLOOR, INITIATION_POINTS: who may start Night Veil work
      (initiation).
    - inheritance_floor, INHERITANCE_FLOOR, tier_rank: tier inheritance (inheritance).
"""

from hivemind.guard.policy.floors.chain import FLOORS, floor_refusal
from hivemind.guard.policy.floors.hive_state import (
    ENTRY_POINT,
    ENTRY_POINT_PREFIX,
    ENTRY_POINTS_FLOOR,
    LOOPBACK_FLOOR,
    STATE_PATHS_FLOOR,
    hive_state_floor,
)
from hivemind.guard.policy.floors.inheritance import INHERITANCE_FLOOR, inheritance_floor, tier_rank
from hivemind.guard.policy.floors.initiation import (
    INITIATION_FLOOR,
    INITIATION_POINTS,
    initiation_floor,
)
from hivemind.guard.policy.floors.night_veil import (
    CLEARANCE_FLOOR,
    CONTROL_LINK_FLOOR,
    LOCAL_SLOTS_FLOOR,
    LOCATION_FAMILIES,
    LOCATION_FLOOR,
    ONION_SUFFIX,
    REMOTE_DNS_SOCKS_SCHEMES,
    VIRTUAL_ONLY_FLOOR,
    is_night_veil,
    night_veil_floor,
)
from hivemind.guard.policy.floors.refusal import (
    STATE_FLOOR_RULE,
    TIER_FLOOR_RULE,
    Floor,
    FloorRefusal,
)

__all__ = [
    "CLEARANCE_FLOOR",
    "CONTROL_LINK_FLOOR",
    "ENTRY_POINT",
    "ENTRY_POINTS_FLOOR",
    "ENTRY_POINT_PREFIX",
    "FLOORS",
    "INHERITANCE_FLOOR",
    "INITIATION_FLOOR",
    "INITIATION_POINTS",
    "LOCAL_SLOTS_FLOOR",
    "LOCATION_FAMILIES",
    "LOCATION_FLOOR",
    "LOOPBACK_FLOOR",
    "ONION_SUFFIX",
    "REMOTE_DNS_SOCKS_SCHEMES",
    "STATE_FLOOR_RULE",
    "STATE_PATHS_FLOOR",
    "TIER_FLOOR_RULE",
    "VIRTUAL_ONLY_FLOOR",
    "Floor",
    "FloorRefusal",
    "floor_refusal",
    "hive_state_floor",
    "inheritance_floor",
    "initiation_floor",
    "is_night_veil",
    "night_veil_floor",
    "tier_rank",
]
