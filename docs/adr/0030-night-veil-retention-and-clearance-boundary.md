# ADR-0030: A Night Veil Cell is attested from its image, keeps only a lifecycle skeleton after teardown, and exports nothing but labelled Honey

- Status: Accepted
- Date: 2026-09-21

## Context

Night Veil is the Comb Shield tier for work that must not be linkable to the operator: Virtual
only, all web traffic through OpenVPN plus Tor with direct egress blocked, every model slot local,
and a Waggle control link that reaches the Hive Stand through a Tor hidden service (codingrules
8.7). Three things about it are hard to reverse once code depends on them: what "ready" means for
such a Cell, what record of its work the Hive keeps, and what data may cross its boundary in
either direction. Codingrules 12 already lists the surviving events; this ADR records why, and
fixes the rest.

## Decision

**Readiness is attestation of an image, never configuration of a Cell.** A Night Veil Cell boots
from `images/night-veil-ubuntu`, which carries the OpenVPN client, the Tor daemon, Tor Browser,
the nftables kill-switch and the location-blind defaults. `hive/night_veil.py` runs a fixed list
of deterministic checks before `CellReady` is accepted: kill-switch active, default route through
the tunnel, Tor healthy, Tor Browser launchable, the Hive Stand's hidden service reachable with
the Waggle socket on the Tor SOCKS proxy and not the VPN interface, DNS leak checks green, direct
egress blocked, geolocation denied, metadata endpoints unreachable, timezone UTC, locale pinned,
WebRTC local-IP probe blocked. Nothing is installed at runtime. Any red check fails the placement,
records `cell.attested` with the per-check result, and destroys the Cell. There is no degraded
mode and no retry that skips a check.

**Placement is constrained before anything is provisioned** (`queen/placement/policy.py`): Virtual
only, only on a human-originated request, never autonomously escalated to by the Queen or a Warden,
network profile `VPN_TOR`, and a hosting plan whose every slot resolves to a local provider with
no Hive Stand or hosted fallback. A plan that cannot be made local-only fails placement; it never
spills.

**The lifecycle has no Overwinter branch:** `provision → Warden ready → grant → teardown`, enforced
by the policy function, the state machine and the pool (ADR-0029).

**After teardown the Hive keeps a skeleton, not a story.** The Cell's execution records live in an
ephemeral trail segment keyed to the Cell and are purged at teardown together with the VPN
gateway's, Tor daemon's and hidden-service logs for that Cell. What survives on the Queen's trail
is exactly the list in codingrules 12: `cell.provisioned`, `cell.attested`, `queen.placed` with
the id of the human request, `forage.granted`, `forage.plan_written`, `cell.sting_cut`, task state
transitions carrying only the task id, one `capping.summary` per tier, and `cell.destroyed`. The
skeleton exists so the operator can still answer "what did my Hive do and what did it cost" and so
the Undertaker can prove the Cell is gone.

**Clearance is enforced in both directions.** A Night Veil Cell may read and write Wildflower (C0)
and Apiary (C1) and never Royal (C2): `memory.assemble` filters by the principal's allowance, so a
Night Veil bee is never shown a personal detail it could leak. The one intentional export is Honey
the work ripened on the Cell's own local slots at C0 or C1, deposited on purpose and labelled
`origin_tier = NIGHT_VEIL`. Leavings are always `DENY` on this tier.

## Consequences

Positive: "Night Veil" is a checkable claim; a failed attestation is a per-check trail record, not
a silent downgrade. Nothing personal can enter the Cell, and nothing but chosen, labelled knowledge
leaves it. Cost and audit remain answerable.

Negative: debugging a failed Night Veil task after teardown is close to impossible by design; the
operator reproduces it on a Propolis Cell. Local-only slots mean the tier is unusable on a host
that cannot run a model. The hidden-service control link is slow and flaky, which Clustering and
outbox replay absorb. The image depends on `desktop-ubuntu` (6.1), so it is the last phase 5 step
to land, and attestation is developed against a fake probe until it does. None of this defends
against an adversary who can see the Cell's own host.

## Alternatives considered

Configuring VPN and Tor at provision time: a window in which the Cell has direct egress, and a
runtime install whose result cannot be attested deterministically. Keeping the full trail
encrypted: retention is retention; a key can be compelled. Keeping nothing at all: the operator
could not account for spend, and an orphaned Cell could not be proven destroyed. Sharing the VPN
tunnel for the control link: lets an observer at the tunnel's exit link the anonymised work to a
known Hive Stand address (codingrules 8.7).
