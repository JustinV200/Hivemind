# ADR-0031: One capability grammar, attenuated down the tree, checked at named enforcement points

- Status: Proposed
- Date: 2026-09-24

## Context

Phase 3 built a small capability core (`guard/capabilities.py`: tool, filesystem, network, exec,
device and spend families) because the Drone's tools needed it, and left the rest of the model to
phase 10. Reading the tree before this phase found the consequences of that gap: a Warden's set is
only its lease's access-level ceiling, so nothing the Queen decides narrows it; tool invocation
never checks `tool:<name>`; any attached Warden can top up any grant; a Queen-sent rebind is never
checked against the grant; a tool refused for lack of a capability leaves nothing on the trail;
and placement cannot express "this goal may not use the Hive Stand". Phase 10 must say, for every
action that changes state, who may do it, why not, and where that was decided, and the grammar is
hard to reverse once capability strings are stored on approved devices and tasks.

## Decision

**One grammar: a family, then a scope when the family takes one.** A capability is written
`family` or `family:scope`. Each family declares its scope kind, and matching follows it:

| Kind | Families | A held capability satisfies a needed one when |
|---|---|---|
| flag (no scope) | `tool:request`, `cell:virtual`, `cell:hive_stand`, `exoskeleton`, `exoskeleton:real_display`, `honey:write`, `wax:propose`, `warden:spawn`, `forage:request`, `question:human`, `observe`, `observe:thoughts`, `entrance:submit`, `entrance:answer`, `entrance:push`, `entrance:steward`, `supersede`, `sting_cut`, `wifi:scan`, `host:metadata` | the family is the same |
| glob | `tool`, `fs:read`, `fs:write`, `exec`, `cell:outside_scratch` | `fnmatch` over the POSIX form matches |
| prefix | `net`, `device`, `cell:real`, `honey:read`, `observe:honey`, `geo`, `watch`, `tool:scope` | equal, or the held scope ends in `*` and prefixes the needed one |
| enumerated | `llm` (a `ModelSlot`), `cell:comb_shield` (a `CombShieldLevel`), `tactic` (`write_like_human`, `mouse_like_human`) | equal, or the held scope is `*`; values are the lowercase member names |
| ordered | `honey:clearance` (`c0 < c1 < c2`) | the held level is at or above the needed one |
| amount | `spend` (USD) | the held amount is at or above the needed one, `*` being unlimited |

Parsing takes the **longest** matching family prefix, so `tool:request` and `tool:scope:cell` are
their own families rather than tools named `request` or `scope:cell` (those tool names are
reserved), and `observe:honey:hive` is not `observe`. A flag family's string is exactly its name;
`observe:anything-else` is invalid rather than silently widened. `CapabilitySet` keeps `allows`,
`attenuate` and `issubset`, gains nothing that widens, and every string it accepts round-trips.

**Principals and where their sets come from.** The principals are the operator, the Queen, a
Warden, a Worker (by role), a Swarm device and an enrolled client device. `[guard]` in the Hive
Manifest gives each role a default set, a hive-wide deny list and the escalation rules; the
shipped defaults are a TOML file in `guard/defaults/`, read through `importlib.resources`, which a
manifest's `[guard]` table overrides. Sets only
narrow down the tree: the Queen's set is the widest; a Warden's is its role default, attenuated
from the Queen's and narrowed by its Cell's access level and Comb Shield tier; a Worker's is its
role default plus what its task needs, attenuated from its Warden's. A goal carries the set of the
principal that submitted it (an enrolled device's approved set, or the operator's for the local
CLI), and every task planned from it inherits that set, which placement reads: a task whose set
lacks `cell:hive_stand` never lands on the Hive Stand, whatever `prefer` says.

**Access levels narrow only what touches the Cell.** `guard/access.py` names the families that act
on the machine (`fs:*`, `exec`, `net`, `device`, `cell:outside_scratch`, `exoskeleton*`,
`tactic:mouse_like_human`, `geo`, `wifi:scan`, `host:metadata`, `watch`) and, per `AccessLevel`,
the ceiling for them. Narrowing a set to a level keeps every other family untouched, so a
`READ_ONLY` Warden still holds `question:human` and `llm:warden`, and can never hold a write, an
exec or a network scope.

**Policy is a pure function with a reason.** `guard.evaluate(request) -> PolicyDecision` takes the
principal, the enforcement point, the needed capability, the held set and the context (the Cell's
tier and access level, the task's bound tier, the request's origin), and returns allow or deny
with the rule that decided and a reason sentence. Rules run cheapest and hardest first: tier
floors (Night Veil and location guardrails), the access-level ceiling, the deny list, then the
held set. Escalation rules map a denial to what happens next (return the refusal to the bee,
raise an Alarm to its supervisor, ask the human). Nothing in the rule order can turn a deny into
an allow except holding the capability.

**Every state-changing action has a named enforcement point.** `guard/points.py` holds the
`EnforcementPoint` enum: placement, lease creation, grant issue, Forage request, Warden spawn,
tool invocation, session calls outside scratch, Exoskeleton attach on a real display, Honey
access, slot binding and rebinding, question routing to the human, Nuc promotion, device
commands, tactic invocation, Comb Shield egress policy activation, and each Entrance route. Each
point is checked through one adapter that calls `evaluate` and, on a denial, records a
`guard.denied` trail event carrying the principal, point, capability, rule and reason, never
content. Because codingrules 12 already makes every state-changing action a trail event, the
enumeration test classifies **every trail event kind** as either authorised at a named point or
not an action (an outcome, an observation); a new kind without a classification fails the test,
and so does a registered call site that no longer names its point. Points whose subsystem is not
built yet (Exoskeleton, Honey, Nuc promotion, device commands, tactics: phases 6, 7 and 11) are
declared now with their phase, so the subsystem wires a point that already exists.

**Tier guardrails are policy floors, not conventions.** A set holding `cell:comb_shield:night_veil`
must also hold `cell:virtual` and may hold no `cell:real:*` and no `cell:hive_stand`. On a Night
Veil Cell every slot binding must be local, the Waggle link must go through the Tor SOCKS proxy to
a `.onion` address, Honey is allowed at `c0` and `c1` and refused at `c2`, and the location
families (`geo:*`, `wifi:scan`, `host:metadata`) are denied by default, with any task plan that
requests them rejected. Night Veil placement needs a human request, identified by the id of the
Entrance request or inbox message that asked for it; neither the Queen nor a Warden can escalate
to it. Dispatch binds a task to its Cell's tier; a runtime path that would weaken a control after
placement is denied against the bound tier, and a task moved to another Cell is re-evaluated and
re-bound before it resumes.

## Consequences

Positive: one string grammar serves the Warden tree, the Entrance's devices and the stored task
sets; "why was this refused" is always a trail row with a rule and a reason; a new kind of action
cannot ship without someone deciding where it is authorised. The gaps listed in the context close
as enforcement points: a Warden may only request Forage for its own grant and only with
`forage:request`, a rebind must stay inside the grant, a tool must be held to be invoked.

Negative: the grammar has six matching kinds, which is more to learn than "glob everything";
longest-prefix parsing reserves two tool-name shapes. Tasks now carry a capability set, so the
Brood Chamber stores one more column. Classifying every trail kind is a small tax on every future
event, which is the point.

## Alternatives considered

Glob matching for every family: `honey:clearance:c*` would silently mean "all clearances" and
`observe*` would match `observe:thoughts`. Role checks (`if principal.role == "warden"`) instead of
capabilities: not attenuable and invisible to the operator approving a device. A policy language
(Rego, Cedar): a runtime and a second syntax for a rule set small enough to be data plus a pure
function. Enumerating enforcement points by call site alone: nothing would notice a new action
added without one; the trail catalogue is the one list every action already has to join.
