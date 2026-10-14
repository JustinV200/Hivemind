"""Compose the Hive Stand's side of one gateway from the requests the contract defines.

`Driver` is what a conformance test holds: `gateway.driver.open_lease()`, `.run_exec()`,
`.put_file()`, `.observe()` and the rest, each sending the message the gateway contract names as
the address the contract says sends it. The requests are grouped by what they act on, in the three
modules whose classes this one inherits: `leases` (leases, terms, sessions), `files` (transfers)
and `programs` (execs, background processes, observation); `calls` is the plumbing under them all.
The split keeps each module small enough to read at once and leaves a test one object to call.

Fits into the Hive:
    Test infrastructure (codingrules section 14.3), not shipped. Held by `Gateway` in
    `contracts.gateway.harness` and used by the gateway conformance suite.

Key invariants:
    - This class adds nothing of its own: it is the union of the three request groups, so there
      is one place to look for each request.

See Also:
    - contracts.gateway.calls for the plumbing every request shares.
    - docs/waggle/gateway-contract.md sections 8 to 12 for the requests themselves.
"""

from __future__ import annotations

from contracts.gateway.files import FileCalls
from contracts.gateway.leases import LeaseCalls
from contracts.gateway.programs import ProgramCalls

__all__ = ["Driver"]


class Driver(LeaseCalls, FileCalls, ProgramCalls):
    """The Hive Stand's side of one gateway: requests out, frames and refusals back."""
