"""Define FakeResolver: a Resolver that answers from a table, so no test ever performs a lookup.

`hivemind.guard.net.resolve.Resolver` is the Guard's one seam to the network; codingrules 14.4
wants an honest fake beside it rather than a mock. `FakeResolver` answers a name from the table
it was built with, answers any other name with its `default` addresses when it has some, and
otherwise fails exactly as the system resolver does for an unknown name (`socket.gaierror`, an
OSError). It records every lookup, so a test can prove a literal was never looked up or that a
refused request never resolved at all. The default answer is a documentation address
(`DEFAULT_ANSWER`, RFC 5737's TEST-NET-3): public-looking, never loopback, link-local or a Hive
Stand address, so a test that only cares that a name resolves somewhere harmless gets exactly that.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy), inside `hivemind.guard.net`. Used by
    tests and by the test builders' default `WorkerContext`; production uses
    `hivemind.guard.net.resolve.system_resolver`. Calls into `ipaddress`, `socket` and
    `.addresses` only.

Key invariants:
    - Never touches the network: every answer comes from the table or `default`.
    - Names are looked up case-insensitively and without a trailing dot, like DNS itself.

See Also:
    - hivemind.guard.net.resolve for the Resolver protocol this implements.
"""

from __future__ import annotations

import ipaddress
import socket
from collections.abc import Mapping, Sequence

from hivemind.guard.net.addresses import IPAddress, normalise_host

DEFAULT_ANSWER = ("203.0.113.10",)  # RFC 5737 TEST-NET-3: a harmless, never-forbidden address.

__all__ = ["DEFAULT_ANSWER", "FakeResolver"]


class FakeResolver:
    """Answer lookups from a table (then `default`), recording every name asked for."""

    def __init__(
        self,
        answers: Mapping[str, Sequence[str]] | None = None,
        default: Sequence[str] | None = DEFAULT_ANSWER,
    ) -> None:
        """Build a resolver with a fixed table.

        Args:
            answers: Host name to the addresses it resolves to, as strings; a name mapped to an
                empty sequence resolves to nothing.
            default: What every name missing from `answers` resolves to; None makes such a name
                fail to resolve, as an unknown name does on a real resolver.
        """
        self._answers = {
            normalise_host(name): tuple(ipaddress.ip_address(text) for text in addresses)
            for name, addresses in (answers or {}).items()
        }
        self._default = (
            None if default is None else tuple(ipaddress.ip_address(text) for text in default)
        )
        self.lookups: list[tuple[str, int]] = []

    async def __call__(self, host: str, port: int) -> tuple[IPAddress, ...]:
        """Return `host`'s addresses from the table, or `default`, recording the lookup.

        Args:
            host: The name being resolved.
            port: The port the connection will use; recorded, never used to choose an answer.

        Returns:
            The table's answer for `host`, else `default`.

        Raises:
            socket.gaierror: `host` is not in the table and there is no `default`.
        """
        self.lookups.append((host, port))
        answer = self._answers.get(normalise_host(host), self._default)
        if answer is None:
            # The same error, and errno, a real resolver gives an unknown name.
            raise socket.gaierror(socket.EAI_NONAME, f"no fake answer for {host!r}")
        return answer
