# hivemind end-to-end tests

Whole-Hive scenarios that run the Queen, at least one Warden and its Workers together, the way
`hive run "goal"` would from the CLI. These are the slowest and fewest tests in the tree; they
exist to catch problems that only show up when every layer runs at once.
