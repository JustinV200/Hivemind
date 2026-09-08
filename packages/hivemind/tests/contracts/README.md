# hivemind contract tests

Protocol contract suites, parametrised over every implementation of a given protocol (every
`CellBackend`, every `LLMProvider`, every `Snapshotter`, and so on) so a new implementation is
proven to honour the same invariants as the ones already in the Hive before it ships.
