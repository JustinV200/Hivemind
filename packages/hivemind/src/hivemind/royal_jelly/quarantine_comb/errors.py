"""Define the Quarantine Comb's own errors: a refused sandbox and a path that escapes one.

The Quarantine Comb (the sandbox every new tool must pass before promotion) fails in two ways
that are not a failed gate. A sandbox may be refused outright (a borrowed Real Cell can never be
one: the Comb runs untrusted generated code, and a borrowed device is left exactly as found), and
a caller may hand a sandbox a path that tries to leave its root. Both are programming or
composition mistakes, not verdicts about a tool, so they are exceptions rather than report
entries. The errors root in `RoyalJellyError` (wave 1B owns `royal_jelly/errors.py`, so these live
beside the code that raises them).

Fits into the Hive:
    Layer 3 (sources of Cells, and capabilities handed down), inside royal_jelly.quarantine_comb.
    Raised by `SandboxCell` (a borrowed Cell) and by every `Sandbox` implementation (an escaping
    path); caught by the composition code that chooses a sandbox. Calls into
    `hivemind.royal_jelly.errors` only.

Key invariants:
    - Each class sets its own stable `code`.

See Also:
    - docs/adr/0047 (the Comb's gates and sandbox) for why a Real Cell is never a sandbox.
    - hivemind.royal_jelly.errors for the root class.
"""

from __future__ import annotations

from typing import ClassVar

from hivemind.royal_jelly.errors import RoyalJellyError

__all__ = ["SandboxPathError", "SandboxRefusedError"]


class SandboxRefusedError(RoyalJellyError):
    """Raise when a Cell cannot be a sandbox: it is borrowed (a Real Cell), not provisioned."""

    code: ClassVar[str] = "hivemind.royal_jelly.sandbox_refused"


class SandboxPathError(RoyalJellyError):
    """Raise when a path handed to a sandbox is absolute or climbs out of the sandbox root."""

    code: ClassVar[str] = "hivemind.royal_jelly.sandbox_path_invalid"
