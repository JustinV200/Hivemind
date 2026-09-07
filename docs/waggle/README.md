# Waggle protocol spec

This directory will hold the Waggle protocol spec: the source of truth for the message shapes that
the Queen (the central orchestrator), Wardens (the per-Cell supervisors) and Workers (the
subagents they spawn) exchange, named after the waggle dance bees use to pass directions to each
other. The spec is written in phase 1.1 and covers the envelope fields every message carries, how
a request, a reply and an event are told apart, the rule for how the protocol's version is
negotiated between two processes, and the shape an error takes when something goes wrong.

Until phase 1.1 lands, this file is a placeholder: there is no spec here yet. Once it exists, the
pydantic models in `packages/waggle/` are generated from it, or checked against it, in CI (coding
rules section 7.7), so the models and this document cannot drift apart silently.
