# Security

HiveMind borrows real machines, makes its own, drives desktops and writes its own tools, so a bug
here can reach a lot. This page is how the Hive keeps that in check: who is trusted with what, where
each rule is enforced, and what is still open. The rules themselves are in
[`.claude/codingrules.md`](../.claude/codingrules.md) section 15, and each one points at the ADR that
decided it.

It was written as roadmap step 14.4, by walking section 15 across the tree. The results of that walk
are at the end.

## Who is trusted

The operator is the only fully trusted party, and they act from the Hive Stand (the machine the Queen
runs on). Everything below them gets a narrower slice:

| Principal | Trusted with | Never trusted with |
|---|---|---|
| The operator | everything, from the Hive Stand's loopback listener | nothing is held back, but break-glass acts still need the password and a typed phrase |
| The Queen | issuing capability sets and Forage grants, placing work, isolating a Cell | running anything herself (ADR-0021), lifting an isolation on her own |
| A Warden | its own Cell, its sub-bees, the Capping gate for their work | widening what the Queen gave it |
| A Worker | the tools, scopes and spend on its capability set | anything it asks for in words |
| An enrolled device | what the operator approved on loopback, under a daily spend cap | approving, unlocking or reopening anything |
| A Swarm device's Pollen Packet | carrying out signed requests inside its lease | deciding anything |

Model output, tool results, web pages, files on a Cell and Honey are all treated as untrusted input,
whoever produced them.

## Grants and attenuation

Every action is checked against a capability, written `family` or `family:scope` (ADR-0039). The
`guard` package parses them, and its Enforcer is the only part that records a refusal. There are two
roots: the operator's ceiling for a device, and the Queen's for a goal. Below a root, a set can only
narrow. `CapabilitySet.attenuate` raises rather than widen, and there is no union. A Worker's set is a
subset of its Warden's, which is a subset of what the Queen issued.

Forage, the Hive's compute, model seats and spend, follows the same shape (ADR-0014). A grant is
computed by one pure function, `forage.allocate.grant`, and it only ever narrows a Cell's raw
capacity: the Royal Reserve comes off first, then headroom, then the tightest of five limits. The
Fanner meters every model call against the grant it was issued under.

The access level of a Real Cell (`READ_ONLY`, `SCRATCH` or `FULL`) caps every set issued for that
Cell, whatever the policy says. Below `FULL`, a command runs only on a Cell that contains its
processes (ADR-0060). On a Hive Stand at the default `SCRATCH`, a task that needs a shell goes to a
Virtual Cell instead.

A Worker cannot get more by asking. A request for a capability it does not hold becomes a question
to its Warden, a `guard.*` event lands on the trail, and the set stays exactly as issued.
`tests/unit/workers/test_injection_invariant.py` holds the Hive to that.

## Leases on Real Cells

A Real Cell is a machine the Hive borrows: the Hive Stand, or a device enrolled in the Swarm. Work
on it happens under a lease (ADR-0010), and the lease owns the bookkeeping:

- A Worker works inside the lease's own scratch directory. A path is judged by the Cell's own path
  rule, so a Swarm device's paths are never resolved against the Hive Stand's disk.
- Anything outside scratch needs an explicit capability, goes through the Capping gate and is
  recorded on the trail with its path. The lease keeps what it must put back.
- `release()` kills every process the lease started, restores what it recorded and removes the
  scratch directory. A release that fails leaves the lease `ORPHANED`, never stuck, so the Undertaker
  can retry it.
- What a task was allowed to keep past release is a Leaving, approved by policy and listed by
  `hive cells leavings list` (ADR-0025).
- A task with `isolation = "required"` never lands on a Real Cell, and a Night Veil task never does
  either.

The lease browser follows the same rule. It reads only its own scratch (ADR-0034), every web request
it makes is held to the bee's `net` scopes, and its own windows stay shut (ADR-0059).

## The Swarm

A device joins through a Pollen Packet, which is a gateway and nothing else (ADR-0051). The trust
model comes down to a few rules:

- An invite is minted on the Hive Stand, used once, and carries the pin of the Swarm listener's
  certificate. The device's first key is bound to that invite. No key is trusted before the one
  signed enrolment frame.
- Every frame is signed with Ed25519 over the envelope and checked for who sent it. Every request is
  then checked for what that sender may do. The packet verifies the Queen's signature before it runs
  anything.
- The replay window drops a frame seen before, and a ceiling bounds frame size (ADR-0055).
- The operator sets the device's access level and Comb Shield tier. Placement never raises a Real
  Cell's tier, and Night Veil is refused on a Real Cell. At Propolis the Warden checks the device's
  own VPN before each lease and holds the task when the check fails. HiveMind never installs or
  reroutes a VPN on a borrowed machine (ADR-0057).
- `hive swarm revoke` ends an enrolment for good. The next beat closes the link, stops the device's
  Warden (which releases its lease) and forgets the key.
- A merged trail segment from a device must carry a signature that verifies against that node's
  key. One that does not is refused and recorded as `guard.segment_refused`.

## Nucs offline

A Nuc is a Swarm device that runs its own Warden and its own model server (ADR-0053). When its link to
the Queen drops, it keeps working on what it already owns, and it cannot grow:

- No new Cells, no new slots and no human-bound questions. A question is held until the Queen is
  back, and a request for shared Forage waits in the outbox.
- Shared grants are frozen from the Warden's side. The Queen settles them when she re-sends them.
- The diagnostic Drone a Nuc spawns to mend its link runs under the Cell's access level and the
  Capping gate like any Drone, inside a reconnect budget carved out of `max_offline_s`. Being cut
  off never grants more access than being connected (ADR-0056).
- Past `max_offline_s` the Warden clusters fully, and a device with no Warden of its own runs the
  dead-man switch.

## The Royal Jelly Lab

The Lab writes tools, and a tool is code a bee will run later, so this is where the Hive is most
careful (ADR-0046, ADR-0047):

- `CombRegistry.promote()` refuses a tool without a passing `QuarantineReport`. There is no `force`
  argument.
- The Quarantine Comb runs inside a sandbox Virtual Cell, driven by that Cell's own Warden, with no
  network, CPU, memory and time limits, and six gates in order where the first failure ends the run.
  A borrowed Cell is refused as a sandbox, so a Real Cell can never be one.
- The subprocess sandbox is for a developer's machine only. The manifest refuses `[royal_jelly]
  sandbox = "subprocess"` unless `[hive] env = "dev"`, and `hive tools quarantine` refuses to run under
  a manifest that is not `dev`.
- A tool is a package named by its sha256, and a promoted version is never changed. Retiring one is
  a mark, not a delete.

## The Exoskeleton

The Exoskeleton gives a bee a display, input, audio and a browser (ADR-0031, ADR-0032):

- A display is private to the lease. The Warden starts its own Xvfb and window manager in scratch
  and stops them when the task ends. Driving the display already running on a Real Cell needs the
  operator's own opt-in, `real_display_allowed`, and a separate capability.
- Every GUI action goes through the Capping gate as typed steps, declares its tier from what it
  reaches, and is rolled back by checkpoint when a declared postcondition fails.
- The flight recorder keeps evidence, not a story, and redacts where the evidence is made.
- Watch mode never captures the screen or input (ADR-0054).

## Prompt injection

Text from outside the Hive can carry instructions, so it is scanned and fenced before any model reads
it (ADR-0043, [`guard/untrusted-content.md`](guard/untrusted-content.md)).

### Tool results and session output

Every tool result and every `run_command` or `read_file` output is
scanned by `ToolRegistry.execute` before the Worker sees it. The scanner bounds, normalises and scores
the text, then passes it, labels it or drops it. A dropped text never reaches the model, only a
notice and the keyed hash that stands in for it on the trail.

### Honey and Nectar

A Honey hit is scanned when it is retrieved and reaches `memory.assemble` as a
`RetrievedItem`, fenced and labelled `RETRIEVED`. A TAINTED or over-clearance hit is refused outright.
Nectar is scanned when it is deposited. Honey is data: it is never `exec`'d or templated into a shell.
Lowering a note's clearance is a proposal the Judge reviews (ADR-0038).

### Hot state

An awake episode is assembled fresh from durable state every time (ADR-0011), so no
conversation builds up where an instruction could hide. Outside text in hot state is rendered by
`memory.hot_state.untrusted`, inside exactly one fence it cannot close, with every delimiter inside
it broken first. A bee caught by an injection has its memory tainted from the suspect episode on, and
a tainted Handoff never travels to another Cell (`guard/tainted-memory.md`).

### The human's own chat

The Queen scans a Landing Board message before she builds an episode around it.

### What gets through

Whatever gets through, the worst an injected instruction can do is make a bee ask. It never widens a
grant, never lands an outside-scratch write uncapped, and always leaves a `guard.*` event.

## The Entrance

The Hive Entrance is the only door, and remote access has its own runbook,
[`runbooks/remote-access.md`](runbooks/remote-access.md). In short (ADR-0041): every client is a
device enrolled with its own key and approved on the loopback listener. Every login is that key plus
the operator's password. Approval, unlock, capability widening and reopening exist only on loopback.
There is no public mode, and every remote mode is TLS on a DNS name. The Entrance Reducer cuts the
remote listener in about a second, and the Guard Bee may reduce on its own because narrowing is
always safe.

## Section 15, walked across the tree

Each rule in codingrules section 15, where it is enforced, and what this pass found. The walk was
done on 2026-10-28 against `finish/roadmap`. "Holds" means the enforcing code is where the
table says and a test covers it.

| Rule | Enforced by | Result |
|---|---|---|
| Least privilege is code | `guard` capability sets, the Enforcer on every dispatch | Holds. The injection invariant test covers asking for more. |
| Tools never skip the Quarantine Comb | `CombRegistry.promote()` needs a passing report | Holds. No `force` path exists. |
| The sandbox is the boundary | `SandboxCell` in a Virtual Cell | Fixed in this pass: the subprocess sandbox was only warned about outside `dev`. It is now refused by the manifest and by `hive tools quarantine`. |
| Subprocess calls use argument lists and carry `# SAFETY:` | `cell/local`, `hive/backends`, `entrance/expose/tunnel.py`, `royal_jelly/.../sandbox_subprocess.py`, `pollen/platform` | No `shell=True` anywhere. Fixed in this pass: three QEMU runner calls had no `# SAFETY:` comment. |
| Device commands are signed | Waggle envelope signing, the packet's verifier | Holds (ADR-0005, ADR-0051). |
| Nothing is executed from Honey | Honey is data, scanned and fenced on retrieval | Holds. No `eval`, `exec` or `pickle` in any package's source. |
| LLM output is untrusted | Tool schemas and capabilities checked before execution | Holds. |
| Blast radius defaults | Virtual Cells: no inbound port, egress by policy. `hive abscond`: password plus typed phrase, no route on the Entrance | Holds. |
| Capabilities and Forage only attenuate | `CapabilitySet.attenuate`, `forage.allocate.grant` | Holds. |
| An offline Warden cannot grow | `wardens/offline/policy.py` | Holds. |
| Watching is bounded by the access level | `wardens/watch/` | Holds. |
| Nothing lands uncapped | The Capping gate, with the proposer never verifying | Holds. |
| Real Cells are borrowed | `RealCellLease`, the left-as-found test | Holds. |
| The Entrance is the only door | `entrance/expose`, the loopback check, the Reducer | Holds. |
| Supersedure never leaves two Queens | ADR-0065, the Queen lock | Holds. |
| Dependencies are audited | CI's `audit` job (`pip-audit --strict`) and `pnpm audit --audit-level=high` | Runs in CI on every push. Not run locally in this pass, since it needs a network lookup. |

## Still open

These are known and written down where the code lives. None of them widens what a bee can do, but
each is worth knowing:

- `[hive] env` defaults to `dev`, and the starter manifest `hive init` writes does not set it, so the
  dev-only gates are on only when an operator writes `env = "prod"`.
- After a Queen restart her replay window is empty, so an Alarm or question resent from a stretch the
  link dropped unnoticed can show up twice. Results and Nectar stay idempotent.
- The Queen sends a device's model ceilings once per attachment, not on every change
  (`queen/forage/ceilings.py`).
- `hive cells release` revokes a lease's grants but cannot itself end the Warden's session.
- Docker and QEMU snapshot ids live in one process's memory, so a `hive cells rollback` cannot see a
  snapshot an earlier, separate `hive cells snapshot` took.
- On a Docker control network, a model server on the host bound to every interface stays reachable
  from an isolated Cell. The Hive logs a warning naming it, and the fix is to bind the server to the
  bridge address or to loopback.
