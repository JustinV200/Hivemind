# hivemind integration tests

Tests that exercise real infrastructure: a real SQLite database, a real Docker daemon for Virtual
Cell backends, and similar. Marked so they can be skipped where that infrastructure is not
available (`pytest -m "not integration"` is the default local and pre-commit run).

- `test_pollen_in_container.py` -- a Pollen Packet in a container (the device image of
  `e2e/swarm/`), enrolled over `wss://` with a pinned certificate, leased through `SwarmSource` and
  driven through `PollenSession`: commands, files in and out of scratch, a background program, close,
  and then the container's scratch, files and process table as they were. It needs a Docker daemon
  and `uv`, not the docker SDK extra, and builds its image on first use.
