"""Re-export the gateway conformance rig: run a gateway, drive it and stand in for the Hive.

The gateway contract (docs/waggle/gateway-contract.md) is a wire protocol, so the suite that
checks it plays the Hive Stand against a gateway it did not write. This package is that player,
split by responsibility (codingrules 5.2): `harness` runs the gateway under test as a subprocess
from a command template and brings it online against a live Hive Stand, `driver` sends it the
requests the contract defines (grouped in `leases`, `files` and `programs` over the plumbing in
`calls`), `wire` replaces the Hive Stand's listener with a bare socket for the clauses about the
bytes themselves, and `machine` asks the machine the gateway runs on what it sees. The cases are
the `test_gateway_contract*.py` modules beside this package.

Fits into the Hive:
    Test infrastructure (codingrules section 14.3), not shipped. Used by the
    `test_gateway_contract*.py` modules of the directory above; builds on `builders.swarm.live`.

Key invariants:
    - This file holds re-exports and `__all__` only.

See Also:
    - contracts/test_gateway_contract.py for the suite's overview and the command template.
    - docs/waggle/gateway-contract.md section 2 for how a gateway author runs the suite.

Public API:
    - COMMAND_VARIABLE, DEFAULT_COMMAND, Gateway, GatewayStartError, start_gateway,
      running_gateway, command_template, parse_command: starting the gateway under test (harness).
    - Driver, ExecRun, RunningExec, ExecOptions, refused: the requests sent to it (driver,
      programs, calls).
    - RawHive, Frame: a bare socket standing where the Hive Stand's listener was (wire).
    - alive, exists, is_directory, names_in, python_argv, read_file, same_path, wait_until,
      write_file: the machine's disk and process table (machine).
"""

from contracts.gateway.calls import refused
from contracts.gateway.driver import Driver
from contracts.gateway.harness import (
    COMMAND_VARIABLE,
    DEFAULT_COMMAND,
    Gateway,
    GatewayProcess,
    GatewayStartError,
    command_template,
    parse_command,
    running_gateway,
    start_gateway,
)
from contracts.gateway.machine import (
    alive,
    exists,
    is_directory,
    names_in,
    python_argv,
    read_file,
    same_path,
    wait_until,
    write_file,
)
from contracts.gateway.programs import ExecOptions, ExecRun, RunningExec
from contracts.gateway.wire import Frame, RawHive

__all__ = [
    "COMMAND_VARIABLE",
    "DEFAULT_COMMAND",
    "Driver",
    "ExecOptions",
    "ExecRun",
    "Frame",
    "Gateway",
    "GatewayProcess",
    "GatewayStartError",
    "RawHive",
    "RunningExec",
    "alive",
    "command_template",
    "exists",
    "is_directory",
    "names_in",
    "parse_command",
    "python_argv",
    "read_file",
    "refused",
    "running_gateway",
    "same_path",
    "start_gateway",
    "wait_until",
    "write_file",
]
