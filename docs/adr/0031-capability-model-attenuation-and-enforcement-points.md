# ADR-0031: One capability grammar, attenuated down the tree, checked at named enforcement points

- Status: Accepted
- Date: 2026-09-24

## Context

Phase 3 built a small capability core (`guard/capabilities.py`: tool, filesystem, network, exec,
device and spend families) because the Drone's tools needed it, and left the rest of the model to
phase 10. Reading the tree before this phase found the consequences of that gap: a Warden's set is
only its lease's access-level ceiling, so nothing the Queen decides narrows it; tool invocation
never checks `tool:<name>`; any attached Warden can top up any grant; a Queen-sent rebind is never
checked against the grant; a tool refused for lack of a capability leaves nothing on the trail;
a task's network needs never reach its Warden, so no Worker ever holds `net`; and placement cannot
express "this goal may not use the Hive Stand". Phase 10 must say, for every action that changes
state, who may do it, why not, and where that was decided, and the grammar is hard to reverse once
capability strings are stored on approved devices and tasks.

## Decision

**One grammar: a family, then a scope when the family takes one.** A capability is written
`family` or `family:scope`. Each family declares its scope kind, and matching follows it:

| Kind | Families | A held capability satisfies a needed one when |
|---|---|---|
| flag (no scope) | `tool:request`, `cell:virtual`, `cell:hive_stand`, `exoskeleton`, `exoskeleton:real_display`, `honey:write`, `wax:propose`, `warden:spawn`, `forage:request`, `question:human`, `observe`, `observe:thoughts`, `entrance:submit`, `entrance:answer`, `entrance:push`, `entrance:steward`, `supersede`, `sting_cut`, `wifi:scan`, `host:metadata` | the family is the same |
| glob | `tool`, `fs:read`, `fs:write`, `exec`, `cell:outside_scratch` | `fnmatch` over the POSIX form matches |
| host | `net` | equal (DNS names case-insensitively, IP literals as addresses); or the held scope is `*`; or it is `*.domain` and the needed host is a strict subdomain on a label boundary; or it is a CIDR network containing the needed address. No other wildcard is valid, so `net:10.0.0.*` cannot mean `10.0.0.1.attacker.example` |
| prefix | `device`, `cell:real`, `honey:read`, `observe:honey`, `geo`, `watch`, `tool:scope` | equal, or the held scope ends in `*` and prefixes the needed one |
| enumerated | `llm` (a `ModelSlot`), `cell:comb_shield` (a `CombShieldLevel`), `tactic` (`write_like_human`, `mouse_like_human`) | equal, or the held scope is `*`; values are the lowercase member names |
| ordered | `honey:clearance` (`c0 < c1 < c2`) | the held level is at or above the needed one |
| amount | `spend` (USD) | the held amount is at or above the needed one, `*` being unlimited |

Parsing takes the **longest** matching family prefix, so `tool:request` and `tool:scope:cell` are
their own families rather than tools named `request` or `scope:cell` (those tool names are
reserved), and `observe:honey:hive` is not `observe`. A flag family's string is exactly its name;
`observe:anything-else` is invalid rather than silently widened. `CapabilitySet` keeps `allows`,
`attenuate` and `issubset`, gains nothing that widens, and every string it accepts round-trips.

**Two roots, and sets only narrow below them.** The principals are the operator, the Queen, a
Warden, a Worker (by role), a Swarm device and an enrolled client device. The operator and the
Queen are separate roots: families that only a human may exercise (`supersede`,
`entrance:steward`) belong to the operator alone, and the device families (`entrance:*`,
`observe*`) to the operator and to the devices approved from that set; no bee ever holds them.
`[guard]` in the Hive Manifest gives each role a default set, a hive-wide deny list and the
escalation rules; the shipped defaults are a TOML file in `guard/defaults/`, read through
`importlib.resources`, which a manifest's `[guard]` table overrides. Below the Queen, sets only
narrow: a Warden's set is its role default, narrowed by its Cell's access level and tier floors;
a Worker's set is its role default plus what its task needs, kept only where the Warden's set,
the goal's set and the floors all allow it.

**A goal carries a ceiling.** A goal carries the set of the principal that submitted it: an
enrolled device's approved set, or the operator's for the local CLI. An approved device's set
therefore has two halves: the access families gate which Landing Board routes it may call, and
the work families (`cell:*`, `tool`, `net`, `fs:*`, `exec`, `llm`, `honey:*`, `question:human`,
...) bound what its goals may do. Every task planned from a goal carries that set, and a task's
Worker gets its role default filtered by the goal's set and its Warden's set. Placement reads it:
a task whose set lacks `cell:hive_stand` never lands on the Hive Stand and one lacking
`cell:virtual` never provisions a Virtual Cell, whatever `prefer` says. A device's spend control
is its daily cap, stored with the device (ADR-0033), not a `spend` capability.

**Access levels narrow only what touches the Cell.** `guard/access.py` names the families that act
on the machine (`fs:*`, `exec`, `net`, `device`, `cell:outside_scratch`, `exoskeleton*`, `geo`,
`wifi:scan`, `host:metadata`) and, per `AccessLevel`, the ceiling for them. Narrowing a set to a
level keeps every other family untouched, so a `READ_ONLY` Warden still holds `question:human` and
`llm:warden`, and can never hold a write, an exec or a network scope. This changes phase 3's
`ceiling_for`: `tool` and `spend` are no longer part of any ceiling, because neither is an effect
on the Cell.

**Policy is a pure function with a reason.** `guard.evaluate(request) -> PolicyDecision` takes the
principal, the enforcement point, the needed capability, the held set and the context (the Cell's
tier and access level, the task's bound or requested tier, the request's origin), and returns
allow or deny with the rule that decided and a reason sentence. Rules run cheapest and hardest
first: floors, the access-level ceiling, the deny list, then the held set. Escalation rules map a
denial to what happens next (return the refusal to the bee, raise an Alarm to its supervisor, ask
the human). Nothing in the rule order can turn a deny into an allow except holding the capability.

**Floors hold whatever a set says.** Floors apply to the context, not to the shape of a set:
- **The Hive's own state.** Every bee is denied `fs:read` and `fs:write` on the Hive's state paths
  (the database and its WAL and SHM files, the secret store, the manifest), `exec` of the `hive`
  and `hivemind-*` entry points, and `net` to loopback and the Hive Stand's own addresses
  (ADR-0033).
- **Night Veil.** When a task's bound or requested tier is `NIGHT_VEIL`: it is placed only on a
  Virtual Cell (its set must hold `cell:virtual`, and no `cell:real` or `cell:hive_stand` it holds
  can place it on a Real Cell); every slot binding must be local; the Waggle link must go through
  the Tor SOCKS proxy to a `.onion` address; Honey is allowed at `c0` and `c1` and refused at `c2`;
  and the location families (`geo`, `wifi:scan`, `host:metadata`) are denied, with any task plan
  that requests them rejected.
- **Night Veil is initiated only by a human, structurally.** Only a goal request carrying an
  explicit `comb_shield = NIGHT_VEIL`, submitted by a device holding `cell:comb_shield:night_veil`
  (or the operator's CLI), may produce Night Veil work. Placement verifies that the goal row says
  so (origin `HUMAN`, requested tier `NIGHT_VEIL`); a chat message never initiates it, because a
  model reading possibly injected text would then be the initiator. If the Queen thinks a goal
  needs Night Veil she may only propose it, and the human's confirmation through the inbox is the
  request. Neither the Queen nor a Warden escalates to it.
- **Tier inheritance.** Dispatch binds a task to its Cell's tier; a runtime path that would weaken
  a control after placement is evaluated against the bound tier and denied, and a task moved to
  another Cell is re-evaluated and re-bound before it resumes.

**Every state-changing action has a named enforcement point.** `guard/policy/points.py` holds the
`EnforcementPoint` enum: placement, lease creation, grant issue, Forage request, Warden spawn, tool
invocation, session calls outside scratch, Exoskeleton attach on a real display, Honey access,
slot binding and rebinding, question routing to the human, Nuc promotion, device commands, tactic
invocation, Comb Shield egress policy activation, each Entrance route, isolation, quarantine, taint
clearing, Sting Cut, Supersedure and Absconding. Each point is checked through one adapter that
calls `evaluate` and, on a denial, records a `guard.denied` trail event carrying the principal,
point, capability, rule and reason, never content. Because codingrules 12 already makes every
state-changing action a trail event, the enumeration test classifies **every trail event kind** as
either authorised at a named point or not an action (an outcome, an observation); a new kind
without a classification fails the test, and so does a registered call site that no longer names
its point. Points whose subsystem is not built yet (Exoskeleton, Honey, Nuc promotion, device
commands, tactics: phases 6, 7 and 11) are declared now with their phase, so the subsystem wires a
point that already exists.

## Consequences

Positive: one string grammar serves the Warden tree, the Entrance's devices and the stored goal
sets; "why was this refused" is always a trail row with a rule and a reason; a new kind of action
cannot ship without someone deciding where it is authorised. The gaps listed in the context close
as enforcement points: a Warden may only request Forage for its own grant and only with
`forage:request`, a rebind must stay inside the grant, a tool must be held to be invoked, and a
task's network needs reach its Worker as `net` capabilities its goal allows.

Negative: the grammar has seven matching kinds, which is more to learn than "glob everything";
longest-prefix parsing reserves two tool-name shapes. Tasks and the `task.assign` message now carry
a capability set, so the Brood Chamber stores one more column and Waggle takes a minor version.
Classifying every trail kind is a small tax on every future event, which is the point. The
state-path floor narrows what a bee's tools can reach on the Hive Stand but cannot parse every
command a bee might run (ADR-0033 records that risk).

## Alternatives considered

Glob matching for every family: `honey:clearance:c*` would silently mean "all clearances",
`observe*` would match `observe:thoughts`, and `net:10.0.0.*` would match a hostname an attacker
controls. Role checks (`if principal.role == "warden"`) instead of capabilities: not attenuable and
invisible to the operator approving a device. A policy language (Rego, Cedar): a runtime and a
second syntax for a rule set small enough to be data plus a pure function. One root with the
Queen at the top: she would hold `supersede`, which codingrules 8.16 reserves for the human.
Enumerating enforcement points by call site alone: nothing would notice a new action added without
one; the trail catalogue is the one list every action already has to join.
