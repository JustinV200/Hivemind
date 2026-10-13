# ADR-0047: The Quarantine Comb runs where its sandbox is, driven by that Cell's Warden, through six ordered gates

- Status: Accepted
- Date: 2026-10-12

## Context

A scaffolded tool is code a model wrote, so it is untrusted until proven otherwise. The Quarantine
Comb is the proof: a sandbox, a fixed list of checks and a report that the Comb Registry
(ADR-0046) requires before it will promote anything. Several things have to be settled together.
Where the code runs: a sandbox that shares a process, a network or a filesystem with the Hive
proves nothing. Who drives it: the Queen holds the orchestration, but no Warden answers `session.*`
messages before phase 11, so she cannot drive a sandbox Virtual Cell's terminal session from her
own process. How a malicious tool is recognised: a model that wants to cheat can write plausible
tests, so the checks cannot be the tool's tests alone. And what to do for a platform the sandbox
cannot imitate: the sandbox is an Ubuntu Virtual Cell, and a tool written for Windows, macOS or
another distribution cannot be fully exercised there.

The roadmap's first draft of step 9.4 had a Warden that needs a sandbox file a `CellRequest` and the
Queen provision one for it.

## Decision

**The Comb runs inside the sandbox Cell, driven by that Cell's own Warden.** The Queen ships the
package to the sandbox Cell as a Basket blob (ADR-0048), sends its Warden a `tool.quarantine`
order that names the blob by hash and carries the spec and the limits, and the Warden runs the Comb
over its own `CellSession` and replies with a `tool.quarantine_report`. A Warden that wants a
sandbox for a cell-scope tool sends the same `tool.quarantine` to the Queen: that message is its
sandbox request. The Queen provisions a sandbox Virtual Cell with no network, runs the Comb there on
the requester's behalf, relays the report back and tears the sandbox down. Both messages arrived in
Waggle minor 1.12, together with `tool.retired`.

**`CellRequest` is not used, and the reason is a boundary.** A `CellRequest` hands a Cell to the
requester for the length of a lease. A sandbox must never be handed to the party whose tool is being
tested: it stays the Queen's, it is torn down after the report, and it appears in no grant. Using a
lease would also have meant a Warden provisioning compute for itself, which the project's rule that
Wardens never provision Cells forbids, and it would have left a sandbox open to the requester's
other bees for as long as the lease lived. Routing the request through `tool.quarantine` keeps the
Queen the only provisioner and keeps the sandbox out of every capability set.

**One sandbox protocol, two implementations.** A `Sandbox` offers a root path inside the sandbox,
a put of bytes, a bounded exec, a read and a cleanup. The Cell-backed implementation wraps a Cell
and its session, runs under a fresh directory below the session's scratch and refuses a borrowed
Cell, so a Real Cell can never be the sandbox. The subprocess implementation is for development
only: a temporary directory, a scrubbed environment, the running interpreter; it is selected only
by an explicit manifest setting and logs a warning at build. It is the one place in the Royal Jelly
package that may start a process.

**Six gates, in this order, and the first failure ends the run.** The static gate is pure analysis
of the source tree: banned imports and names, declared dependencies, the entry point present, tests
present, the spec round-tripping. The tests gate runs the tool's own unit tests. The schema gate
compiles the schema, validates a sample input and refuses an input with an extra property. The fuzz
gate feeds inputs derived from the schema and requires a JSON value or a clean raise, never a hang.
The resources gate holds CPU seconds, peak memory and wall time under the limits. The capabilities
gate maps every session call the run recorded to a capability the spec declares. The report names
the failing gate and the reason.

**A runner inside the sandbox watches the tool, not the platform.** The runner applies resource
limits where the operating system offers them and installs an audit hook that records socket use,
process creation, program replacement and file opens outside the package, the scratch directory and
the interpreter's prefix. A record counts against the tool only when the innermost frame outside the
standard library is inside the package directory. That attribution is the point: a tool that calls
the session's exec, whose own implementation starts a process from inside the Cell code, is clean,
and a tool that starts a process itself is not. The tool is handed a recording session, so the
capabilities gate has the calls it needs.

**A non-Ubuntu target takes a second path, and the report says which.** When the target platform is
Linux with the sandbox image's distribution, the path is the sandbox. Otherwise the static, schema
and fuzz gates run in the Ubuntu sandbox and the tests gate runs inside a lease's scratch directory
on a Real Cell of the target platform, with the flight recorder on where one is attached, at the
device-command tier, and only for a holder of the new flag capability `tool:quarantine_on_real`. The
two roots (the operator and the Queen) hold it; no Warden, Worker or device role does. A hive-scope
tool proved this way is promoted only after the Queen's awake review, not on autopilot. This phase
builds the decision, the capability, the report field and the registry rule; the Real Cell leg is
exercised with a fake session, because no second-platform Real Cell exists in CI.

## Consequences

Positive: the sandbox is exercised through the same session abstraction every other Cell uses;
nothing needs a Warden to answer a terminal protocol that does not exist yet; a malicious scaffold
fails with a reason a human can read; the Queen remains the only party that creates compute; a
tool's use of the session is distinguishable from its own use of the operating system.

Negative: promotion depends on a second Cell's Warden being alive, so a tool cannot be proven while
the sandbox's Warden is down; a sandbox is provisioned per run, which costs seconds the development
subprocess sandbox avoids; the audit hook is a Python-level control and does not contain a native
extension, which is why dependencies are refused outright for now. Because a failing gate ends the
run, a report shows one failure at a time, and a tool with two defects needs two rounds, which the
promotion flow's single retry only partly hides.

## Alternatives considered

The Queen driving the sandbox's terminal herself: no Warden answers `session.*` until phase 11, and
it would put sandbox exec in her process. A `CellRequest` for the sandbox: see above. Running the
Comb in the Hive Stand's process or in a container the Queen owns directly: it shares a failure
domain with the Hive and skips the Cell abstraction. Trusting the tool's own tests as the whole
proof: a generated tool writes the tests it can pass. Static analysis alone: it cannot see what a
dynamic import does. Attributing every audit event to the tool: it would make every legitimate
session call a violation, since the session's implementation starts processes. Running a non-Ubuntu
tool's tests in the Ubuntu sandbox anyway and calling it proven: the report would claim a platform
it never touched.
