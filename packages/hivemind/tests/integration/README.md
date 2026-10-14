# hivemind integration tests

Tests that exercise real infrastructure: a real SQLite database, a real Docker daemon for Virtual
Cell backends, and similar. Marked so they can be skipped where that infrastructure is not
available (`pytest -m "not integration"` is the default local and pre-commit run).

- `test_pollen_in_container.py` -- a Pollen Packet in a container (the device image of
  `e2e/swarm/`), enrolled over `wss://` with a pinned certificate, leased through `SwarmSource` and
  driven through `PollenSession`: commands, files in and out of scratch, a background program, close,
  and then the container's scratch, files and process table as they were. It needs a Docker daemon
  and `uv`, not the docker SDK extra, and builds its image on first use.
- `test_docker_propolis.py` -- the Propolis checks (docs/adr/0057), run for real against containers of
  the Cell image (`hivemind/base-ubuntu:dev`, or `HIVEMIND_TEST_CELL_IMAGE`): the shipped probe over
  a session whose commands run with `docker exec`. The same container is red without a tunnel and
  green once a `tun0` with the whole internet routed into it has been put in its network
  namespace; a resolver on the local network, a program named `tor` and a listener on the SOCKS port
  each turn it red; the recorded attestation names checks and never what the Cell printed; and the
  Cell's own self-attestation, which the image ships, agrees with the one made from outside.
  `docker_tunnel.py` is its rig: the tunnel is made from a short-lived helper container that shares
  the Cell's network namespace (the image has no `ip`), built on first use from `ubuntu:24.04` and
  `iproute2`. It needs a Docker daemon, the Cell image built from this tree and `/dev/net/tun`
  on the daemon's host, and skips without them.
