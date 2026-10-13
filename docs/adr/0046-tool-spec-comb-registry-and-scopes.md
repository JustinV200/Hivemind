# ADR-0046: A tool is a package named by its hash; the Comb Registry promotes only a passing report, at one scope

- Status: Accepted
- Date: 2026-10-12

## Context

Phase 9 lets the Hive extend itself: a Worker or Warden asks for a capability it lacks, the Royal
Jelly Lab (the tool-authoring pipeline) scaffolds a tool, the Quarantine Comb (the sandbox every
new tool must pass, ADR-0047) proves it, and the Comb Registry (the catalogue of promoted tools)
keeps it. Four things are hard to change once tools exist. What a tool is: if it is a loose
module, a row of source text or a name that can be re-pointed, then "the tool that passed the
Comb" and "the tool that runs" can drift apart, and a rollback has nothing exact to return to.
Who may admit one: a registry that any bee can write into is a way around the Quarantine Comb and
around the capability model of ADR-0039. How far a tool is visible: a tool a Warden wrote for its
own machine must not appear in another Cell's menu, and a tool the whole Hive relies on must not
be promotable by a single Warden. And how a tool is described: a tool may ask for more authority
than the bee that requested it holds, and a model-facing description is untrusted text that must
reach a Worker only inside a bundle its Warden chose.

The Basket (ADR-0048) already gives the Hive content-addressed, signed blobs that carry bytes and
no permission. That makes a package a natural blob; the open question is what the registry stores
and what promotion means.

## Decision

**A tool is a package of files named by its sha256.** The package is a deterministic archive: paths
sorted, timestamps fixed, no absolute paths and no parent references, a bounded total size. It
holds the entry-point module, the tool's own unit tests and the specification as a JSON file. The
archive bytes live in the Basket as a blob; the registry stores the hash, never the bytes. One hash
therefore names one immutable version everywhere: the registry row, the digest a `tool.promoted`
announces, the quarantine report that proved it, and the resident copy a Warden loads. A loader
verifies the hash before it unpacks anything, so the channel that delivered the bytes is never
trusted.

**The specification is data, validated purely.** A `ToolSpec` carries the tool's name, a semantic
version, the model-facing description, an input schema whose top level must be an object that
forbids additional properties, the capabilities the tool needs, a single entry point, the third
party packages it imports, the operating systems it supports, its scope and, for a cell-scope tool,
the one Cell it is for. A pure validator rejects a spec that requests a capability its requester
does not itself hold, a schema that is not a closed object, a malformed entry point, a cell scope
with no Cell, and any `tool:` capability among the requirements, because a tool never grants itself
other tools. Dependencies are declared but, in this phase, a non-empty list fails the static gate:
the sandbox has no network, so it cannot install them.

**Two scopes, two promoters.** A tool is promoted at hive scope by the Queen alone, visible
everywhere its supported systems run, or at cell scope by the Warden of the Cell it is for, visible
only to that Cell's bees. The registry's `promote` is the only writer that creates a promoted row,
and it refuses a report that did not pass, a report whose spec or package digest differs from what
is being promoted, hive scope from anyone but the Queen principal, cell scope from anyone but the
owning Warden principal, a version that already exists for that name, scope and Cell, and a report
from the real-Cell quarantine path at hive scope that the Queen's awake review has not accepted. A
test proves each refusal and that the store exposes no other route to a promoted row: there is no
force flag. Principals are values the receiving side builds from what it knows, never from a
message: a remote Warden's principal comes from its link, not its payload.

**Versions are immutable and retirement is a mark.** Retiring a version sets its state and keeps its
row and package as evidence. A rollback makes an earlier promoted version current again and is
recorded as a promotion that names the version it displaces; the earlier row's own passing report is
its evidence, so a rollback needs no new quarantine. The tool state machine is the one in codingrules
Appendix C: requested, scaffolded, quarantining, then promoted or rejected, and promoted to retired,
with a single transition table.

**Discovery and authority stay separate.** Honey describes a tool so a bee can find it; the registry
is the source of truth for what may run. A Worker never enumerates the registry: its Warden hands it
a bundle attenuated from the Queen's grant at spawn, and a promotion changes a running Worker's
bundle only when its Warden re-attenuates it as an intervention.

## Consequences

Positive: the thing that passed the Comb, the thing the registry names and the thing a Worker loads
are the same bytes, checkable by one hash; a rollback is exact; a Warden's tool cannot leak to
another Cell or become hive-wide without the Queen; the registry's refusals are few, testable rules
rather than conventions; a spec cannot widen its requester's authority.

Negative: a tool with third-party dependencies cannot be promoted until the sandbox can install
them, which is a deliberate restriction rather than an oversight. Every version, retired or not,
keeps its package in the Basket, so storage grows with the number of versions; retirement does not
reclaim it. The registry shares the Hive's one database, so a corrupt database takes the registry
with it; the packages themselves are verifiable by hash and survive in the Basket.

## Alternatives considered

Storing source text in the registry row: it loses the tests and the specification as one unit, has
no exact thing to hash and invites editing in place. A mutable "latest" pointer instead of immutable
versions: the report would describe something other than what runs after an edit. Letting any
Warden promote at any scope: one compromised Warden could install code into every Cell. Letting the
Queen alone promote at every scope: it makes the Queen the bottleneck for tools a single machine
needs and gives a Warden no authority over its own Cell. Giving a tool its own capability to add
other tools: it would let a generated tool widen its own reach. Letting a spec ask for what its
requester lacks and trusting the Comb's capability audit to catch it: the audit sees what a tool
does, not what it was entitled to ask for, and the cheapest place to refuse is before any code runs.
