# hivemind contract tests

Protocol contract suites, parametrised over every implementation of a given protocol (every
`CellBackend`, every `LLMProvider`, every `TranscriptionProvider`, every `Snapshotter`, and so on)
so a new implementation is proven to honour the same invariants as the ones already in the Hive
before it ships.

## The gateway conformance suite

`test_gateway_contract*.py` is the other kind of contract suite: instead of one protocol with many
implementations in this process, it checks one wire contract (`docs/waggle/gateway-contract.md`) on
a gateway that runs as a subprocess. It plays the Hive Stand with the real swarm listener on a
loopback socket (`builders.swarm.live`), starts the gateway from the command template in
`HIVEMIND_GATEWAY_COMMAND` (the Pollen Packet when it is unset), enrols it with a minted invite and
sends it the contract's requests as the Queen and the Warden. The rig is the package `gateway/`
(`harness` starts the gateway, `driver` sends requests, `wire` swaps the listener for a bare socket
when a clause is about the bytes, `machine` looks at this machine's disk and process table). The
docstring of `test_gateway_contract.py` is the one place the template's placeholders are defined.

`pollen_harness.py` registers `PollenSession` in the `CellSession` suite against the real packet,
run in this process on the same live Hive Stand.

`test_pollen_warden_env.py` and `test_pollen_warden_process.py` are contracts between two packages
that cannot import each other. The first feeds the environment `pollen.bootstrap` gives the Warden it
starts on a colonized device, unchanged, to the code that reads it in `hivemind.cli.in_cell.borrowed`,
so a renamed variable or a key written in another encoding on either side fails there. The second
starts the real `hivemind-in-cell` console script with pollen's own `WardenKeeper` against a scripted
swarm listener, checks the frames it announces itself with, withdraws it with SIGTERM and checks it
left by its own hand and gave the device back.
