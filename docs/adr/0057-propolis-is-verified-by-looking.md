# ADR-0057: Propolis is verified by looking: one set of tunnel checks for a Real and a Virtual Cell, and Tor's absence on a Virtual one

- Status: Accepted
- Date: 2026-10-14

## Context

Propolis is the Comb Shield tier (a Cell's security posture, separate from its access level) for
normal production work: the Cell's traffic leaves through a VPN tunnel and Tor is not in use. On a
Real Cell, a borrowed device, the operator's own tunnel is what makes the claim true, so the Hive
looks before it places work and never installs, starts or reroutes anything on a machine that is
not its own; that is the Propolis check built in phase 11 (`swarm.shield`), and no ADR recorded
it. On a Virtual Cell, a machine the Hive builds from an image, the claim was still only a
label: a PROPOLIS Cell became ready with nobody having looked at it, and the in-Cell Warden refused
the tier outright. Only Night Veil, the strictest tier, was attested before ready (ADR-0030). The
roadmap's exit criterion for phase 11 closes the gap in one sentence: "On a Virtual Cell the same
checks run against the image, with Tor disabled for that tier." Four things in it are hard to
reverse once code depends on them: whether a Virtual Cell's checks are the Real Cell's or a copy,
what makes a Cell ready when no probe is configured, how the audit event names the tier it
attests, and what the Cell itself says about its tier.

## Decision

**The checks are fixed commands and pure judges, and one set serves both kinds of Cell.** A tunnel
check reads a Cell through its own session (the terminal every Cell offers) with commands chosen
from a table by operating system and never built from a request or a model: on Linux `cat
/proc/net/route`, `cat /proc/net/ipv6_route` and `cat /etc/resolv.conf`, on macOS `netstat -rn` and
`scutil --dns`. Four pure functions judge the text: the default route goes through a tunnel
interface, every DNS server is reached through it, there is no direct IPv6 default route, and
direct egress is refused. A command that cannot be run, or that fails, contributes nothing and its
check fails; an operating system with no table fails a `platform_supported` check. The code is
`hivemind.cell.tunnel`, moved down from `hivemind.swarm.shield` (which keeps its public names),
because the swarm and the Virtual Cell side are siblings in the layer table and may not import each
other.

**On a Virtual Cell the same checks run against the image before the Cell is ready, plus one: Tor
is not running.** After the Cell's Warden has said hello and before the provider marks the Cell
ready, the Cell is attested at the tier it was provisioned at. The choice is a table from tier to
attestation (`hivemind.hive.attestation.CellAttestation`), so the provider asks one question of a
Cell and never branches on a tier, a backend's name or a Cell's kind. A PROPOLIS Cell runs the four
tunnel checks and `tor_disabled`: no process named `tor` or `tor.real`, and no socket listening on
Tor's usual ports (9040, 9050, 9051, 9150, 9151), read with `ps -eo comm=`, `cat /proc/net/tcp` and
`cat /proc/net/tcp6`. A Cell that cannot give the process list or the IPv4 table fails the check,
because Tor's absence is granted only on a positive look. A red check, or a probe that cannot run,
tears the Cell down and refuses the placement. A MEADOW Cell promises nothing about its network and
is not attested; a NIGHT_VEIL Cell keeps its own thirteen checks unchanged. Real Cells do not get
the Tor check: what else runs on a borrowed device is its owner's affair.

**Attestation looks and never configures.** This is ADR-0030's rule carried to a second tier. The
Hive runs only read-only commands, so nothing is installed, started or rerouted in the Cell, and a
PROPOLIS image that ships without its VPN client fails at its first Cell instead of being quietly
repaired into passing.

**No probe, no Cell.** A probe needs a live session on the very Cell being attested, which only a
composition root can open. Until the Queen has a way to open one on a Virtual Cell, the real
`hive run` hands the provider a probe factory that refuses, for PROPOLIS as for NIGHT_VEIL, so a
PROPOLIS Cell is never made ready on an unchecked word. A tier that needs a probe and has none
configured is refused with a `ConfigurationError`, not skipped.

**One event, win or lose, naming its tier.** `cell.attested` is recorded once per attestation with
every check's name, verdict and short reason, the red checks, `passed`, and now `comb_shield`. The
detail is a fixed reason code or sentence and never text the Cell printed, because the event is
kept. The Night Veil boundary reads `cell.attested` rows to know which Cells were Night Veil (their
records are purged to a skeleton at teardown), so a PROPOLIS attestation must not make a Cell look
like one: a row counts as Night Veil's when it names NIGHT_VEIL, names nothing (a row written
before this ADR) or names a tier the reader does not know, the strictest reading, and not when it
names another tier.

**In the Cell, PROPOLIS is accepted and the Cell reports on itself.** `HIVEMIND_COMB_SHIELD=PROPOLIS`
is a valid bootstrap. The link is the ordinary endpoint: Tor is disabled at this tier, so an onion
Queen URL stays Night Veil's alone and no SOCKS proxy is needed. The wire model refuses a
`CellReady` for any tier above MEADOW that carries no attestation, so a PROPOLIS Cell announces the
four tunnel checks it makes of its own routes and resolvers with the same pure judges. That is a
report and not a gate: a Cell with no tunnel still announces, its checks failed and its heartbeat
saying the shield is not verified, and the Hive's attestation from outside is what decides
readiness.

## Consequences

Positive: a Virtual Cell's tier is a checked fact and not a label, by the very checks a Real
Cell's is, so the two cannot drift and a fix to a judge reaches both. A PROPOLIS image that lacks
its tunnel, leaks its resolver or carries a Tor client is stopped before it is given work, and the
trail says which check refused it. The checks were run for real against a container of the base
image (`tests/integration/test_docker_propolis.py`): the same container is red without a tunnel and
green with a `tun0` carrying the whole internet, and a Tor process or listener turns it red.

Negative: no PROPOLIS Virtual Cell can become ready in a real `hive run` yet. The Queen has no
session path to a Virtual Cell, so the composition root's probe refuses; the shipped templates
are MEADOW only, so a task that needs PROPOLIS finds no Virtual spec (placement takes only a spec
that provisions the tier a task needs, never a MEADOW one, and none at NIGHT_VEIL outside Night
Veil's own path) and waits for a PROPOLIS device; the Docker backend grants
`NET_ADMIN` only to the Night Veil network profile; and no PROPOLIS image ships. Each is a step on
its own, and the fail-closed default is the honest state until they land. The attestation is a
look at one moment, so a Cell resumed from Overwinter is attested again before its link is handed
back; a tunnel that drops while the Cell works is the Cell's own heartbeat's business.

## Alternatives considered

A second copy of the checks for Virtual Cells: two sets of judges would read the same
`/proc/net/route` and disagree sooner or later. Configuring the Cell at attestation time to make it
pass: it hides an image that lacks the tunnel and changes a machine the operator believes is
exactly the image, the case ADR-0030 already rejected for Night Veil. Taking the Cell's own
announcement as the proof of readiness: the Cell is the thing under test, and what it says about
itself is a report. Attesting MEADOW as well: the tier promises nothing to check. Not checking Tor
at Propolis: it is half of what the tier promises, and unchecked it would be kept only by whoever
wrote the image. A separate event kind for a PROPOLIS attestation: one fact in two kinds splits
every reader of the trail in two, where a tier in the payload costs one reader one condition.
Passing a Cell when no probe is configured, with a warning: readiness would then be a default and
not something earned, which is what a tier exists to prevent. Keeping the boundary's old rule that
any `cell.attested` row means Night Veil: every PROPOLIS Cell would be purged as if it were one.
