# hivemind.royal_jelly.spec

The spec package holds ToolSpec, the description of a tool a bee has requested before it exists:
what it should do and what it is allowed to touch. Around it sit `validate_spec` (pure refusals
against the requester's own capabilities), the deterministic package format (`pack`, `unpack`,
`sha256_of`), and the Quarantine Comb's report types (`QuarantineReport`, `GateResult`,
`ResourceUsage`, `QuarantineLimits`). See the package README one level up for how they fit
together.
