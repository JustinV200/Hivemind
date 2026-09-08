# hivemind integration tests

Tests that exercise real infrastructure: a real SQLite database, a real Docker daemon for Virtual
Cell backends, and similar. Marked so they can be skipped where that infrastructure is not
available (`pytest -m "not integration"` is the default local and pre-commit run).
