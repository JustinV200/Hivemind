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

Later phases add two more documents here: `enrolment.md` (roadmap 11.1), the one-time-token
handshake by which a device joins the Swarm (the mesh of enrolled Real Cells, existing devices
borrowed for tasks and left as they were found), and `gateway-contract.md` (11.7a), the minimal
message set a Pollen-equivalent written in another language must implement, with the phase 1
conformance suite runnable against it over WebSocket.
