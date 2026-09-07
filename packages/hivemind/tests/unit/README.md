# hivemind unit tests

Fast, isolated tests that mirror `packages/hivemind/src/hivemind/` one-to-one: for example
`src/hivemind/hive/backends/docker.py` is tested by `unit/hive/backends/test_docker.py`. No real
SQLite, Docker or network; collaborators are faked. CI treats a module with no matching test file
here as an orphan and fails the build.
