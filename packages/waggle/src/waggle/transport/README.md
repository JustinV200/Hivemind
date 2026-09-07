# waggle.transport

The transport package defines the Transport protocol that carries signed Envelopes between two
processes, plus the implementations that satisfy it: an in-memory transport for tests and a
WebSocket transport for real Hives. Higher layers depend only on the protocol, never on which
transport is in use.
