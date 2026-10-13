# Waggle protocol spec

This directory holds the Waggle protocol spec. Waggle is the wire protocol the Queen (the central
orchestrator), Wardens (the per-Cell supervisors, a Cell being a unit of compute a task runs on),
Workers (the subagents they spawn) and Pollen Packets (the thin gateway on an enrolled device)
speak to each other, named after the waggle dance bees use to pass directions. `spec.md` is the
source of truth for the envelope every message rides in, the versioning rule, the shape an error
takes on the wire, and the message catalogue: one subsection per message family and one
machine-readable table listing every kind. The registry at
`packages/waggle/src/waggle/messages/registry.py` is the only place in code that list lives, and a
drift test in CI parses the catalogue table and checks it against the registry in both directions
(coding rules section 7.7), so the models and this spec cannot drift apart silently. The transport
and signing decisions the spec rests on are recorded in
`docs/adr/0004-waggle-transport-websocket-json.md` and
`docs/adr/0005-waggle-envelope-signing-and-offline-outbox.md`.

Two more documents sit beside the spec. [`enrolment.md`](enrolment.md) (roadmap 11.1) is the
enrolment ceremony end to end, by which a device joins the Swarm (the mesh of enrolled Real Cells,
existing devices borrowed for tasks and left as they were found): the one-time invite, the signed
first frame, the accept and what each side keeps. [`gateway-contract.md`](gateway-contract.md)
(11.7a) is the minimal contract a Pollen-equivalent written in another language must meet, with the
contract suite `packages/hivemind/tests/contracts/test_gateway_contract.py` runnable against it over
WebSocket through the environment variable `HIVEMIND_GATEWAY_COMMAND`. The decisions behind both are
`docs/adr/0051-pollen-packet-is-a-gateway-and-the-trust-model.md` to
`docs/adr/0056-active-reconnection-diagnostic-drones.md`; the replay window and frame ceiling of
`docs/adr/0055-waggle-replay-window-and-frame-ceiling.md` amend ADR-0005.
