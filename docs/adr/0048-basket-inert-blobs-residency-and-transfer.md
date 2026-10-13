# ADR-0048: The Basket stores inert blobs; bytes travel, permission does not

- Status: Accepted
- Date: 2026-10-12

## Context

Tools arrive as packages, and the Hive already moves other files: an artefact a Worker wants to
keep, a dataset, an installer a task needs on a second Cell. Until now there was nowhere to put
them. Adding one raises four questions that are expensive to get wrong. Does holding a file grant
anything: if a stored script can be run because it is stored, the store is an execution surface.
How does a bee find a file: if the store is listed in a prompt, a Cell holding a thousand files
costs every prompt its tokens, and anything an attacker can name becomes a thing a model reads.
Who may receive what: a laptop's data must not drift into a Night Veil Cell because a blob was
shipped there. And how do bytes cross the wire: Waggle frames are bounded, nodes connect out to the
Queen, and a transfer interrupted by a lost link should not start again from zero.

## Decision

**A new Layer 2 package, `basket`, beside `honey_store`, `memory` and `supervision`.** It imports the
guard (the scanner), the Cell package (the clearance and tier enums), the common primitives, the
trail and Waggle. It never imports `honey_store`: the Honey record every deposit writes goes
through a port that the composition root fills. `memory` and `llm` never import `basket`, enforced
by a forbidden contract, so the context assembler cannot list it.

**A blob is inert.** It is bytes under a sha256, a size, a descriptive media kind, platform tags, a
clearance, its origin Cell, task and bee, a deposit event id, a taint mark and a state of present or
retired. The store has no execute, install or load method. The one reader that turns bytes into
code is the Worker's tool invoker, and it accepts a hash only through an entry the Warden built from
a Comb Registry row (ADR-0046); a hash with no entry is refused and the refusal is a trail event at
the tool-invocation point. Running a script from a blob is an ordinary capped command, installing a
program from one is an outside-scratch write with its restore path, and only a promotion makes a
blob executable as a tool, by naming its hash as the package. A test asserts the store's whole
public surface and that refusal.

**Honey describes, the Comb permits, the Basket stores.** Every deposit writes exactly one Honey
record through the port: a title and a short summary the proposing bee supplied, the hash, the kind,
the platform tags, the clearance and the origin. That record is the only way a bee finds a blob, so
a retrieval returns the few records that match under the token budget and a Cell holding a thousand
blobs costs a prompt nothing. Nothing enters by side effect: a Worker proposes to keep an artefact
with a retain proposal naming it by digest, the Capping gate checks it like any outside effect, and
only once it is applied does the Warden deposit it, in process on the Hive Stand or shipped up to
the Queen from a Cell.

**The clearance floor is restated, not imported.** A blob's label is the higher of the declared
label and a floor: C2 for a blob from a borrowed Real Cell and C0 otherwise. That is the rule the
Honey Store applies to Nectar. Because two siblings in one layer do not import each other, the
Basket restates it in its own pure module, and its test cites the Honey Store's, so a change to one
that is not made to the other fails a test instead of drifting. A blob whose provenance names an
isolated or quarantined source is deposited tainted, using the depositing Warden's taint ledger, and
is never shipped or loaded. Text-like blobs pass the deterministic scanner at deposit and a flagged
one is refused with an injection event; a binary cannot be scanned and is stored as it is, which is
safe only because it is inert.

**Residency is a ceiling per Cell.** Each Cell keeps the blobs it needs resident under the resident
Basket disk ceiling already on the wire in the Forage ceilings. A pure function admits an incoming
blob, evicting the oldest first until it fits, or refuses one larger than the ceiling alone. A
Virtual Cell's resident set lives below its lease scratch and survives Overwintering with the
volume; absconding removes every resident copy.

**Transfer is offer, chunks, acknowledgements.** Waggle minor 1.12 adds `basket.offer`,
`basket.chunk`, `basket.ack` and `basket.refuse`. The offer carries every claim the receiver needs
to refuse before a byte moves: the clearance, the taint, the platform tags, the size and the signed
manifest. Chunks are at most 256 KiB of raw data, base64 text, ordered by offset, so a frame always
fits. An acknowledgement says how many contiguous bytes the receiver holds, which makes the shipment
resumable by hash. The receiver verifies the digest of the whole and the manifest signature before
keeping a byte outside its temporary file; a mismatch is a refusal with the integrity code and a
guard event. Every shipment goes through the Hive Stand, since nodes connect out to the Queen, so a
Warden-to-Warden shipment is two legs. A receiver refuses by rule: a clearance above the Cell's
ceiling (a Night Veil Cell takes nothing above C1, so nothing from a Real Cell), a tainted blob, no
room, an unknown signer, an unsupported platform. A new enforcement point, `basket_transfer`, names
where that check will be wired; the shipment and receipt events are classified against it.

## Consequences

Positive: a stored file can do nothing until a Comb promotion says so; prompts never pay for the
Basket; the Real Cell floor and the Night Veil ceiling hold for blobs exactly as they do for Nectar;
a lost link costs one round trip, and a tampered or truncated blob is discarded before it is kept.

Negative: the clearance floor exists in two places and is held together by a test, not by the type
system. A binary blob is never scanned, so its safety rests entirely on inertness and on the Comb.
A shipment between two Wardens crosses the Queen twice, trading latency for the rule that nodes
only connect out. The Hive Stand keeps a copy of every deposited blob until it is retired.

## Alternatives considered

Storing blobs in the Honey Store: it would put binaries through ripening and embedding, and make the
Honey Store the execution surface the rule above refuses. Importing the Honey Store's floor
function: it creates a sibling dependency the layer table avoids, and ties the Basket to Honey's
schema. A listing of the Basket in the hot state: a thousand blobs cost every prompt, and the names
would be attacker-influenced text. Streaming a blob as one message: it breaks the frame limit and
cannot resume. Peer-to-peer transfer between Wardens: it would need inbound connections the design
refuses. Treating a blob from an isolated source as shippable with a warning: a taint that can be
ignored is a label, not a control.
