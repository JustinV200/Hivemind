"""Fail any socket connect to a non-loopback address, and keep a record that the guard was live.

Offline mode (`[llm] offline = true`) is checked at load time by the manifest, but a load-time check
only catches a wrong URL. A library that dials home, a provider built from a default endpoint or a
fallback that reaches a hosted API would each pass it. This guard is the other half of ADR-0045's
"offline mode is proven, not asserted": for the length of a test, `socket.socket.connect`,
`connect_ex` and `socket.create_connection` are replaced by wrappers that raise `AssertionError`
naming the address for anything that is not `127.0.0.0/8`, `::1` or `localhost`, and call through
for the rest. Every attempt is also recorded, because the code that dials may swallow the error
(an HTTP client turns it into "provider unavailable"): the test asserts afterwards on the record,
not only on the exception reaching it. The record also proves the guard was live, since a Hive that
never touched a socket would pass an empty record for the wrong reason.

Fits into the Hive:
    Test infrastructure (codingrules section 14.5), not shipped. Installed by the `egress` fixture
    of `tests/e2e/offline/conftest.py`; read by `tests/e2e/offline/reads.py`. Calls into `waggle`
    (`is_loopback_host`) only.

Key invariants:
    - The guard sees sockets opened by this process (threads included). A child process, such as
      the Chromium a browser Exoskeleton starts, opens its own and is outside it; ADR-0045 names
      that limit.
    - A Unix-domain socket is local by construction and never counts.
    - A host name other than `localhost` is refused even if it would resolve to loopback: judging
      it would take the lookup the guard exists to forbid.

See Also:
    - docs/adr/0045-provider-routing-forage-aware-fallbacks-and-offline-mode.md for the decision.
    - waggle.uris.is_loopback_host for the loopback test the manifest's own check uses.
"""

from __future__ import annotations

import socket
from dataclasses import dataclass, field

import pytest

from waggle import is_loopback_host

# What `socket` calls an address: a `(host, port, ...)` tuple for the internet families, a path
# (str or bytes) for a Unix-domain socket; the stdlib types it `Any`, so this names the two shapes.
_Address = tuple[object, ...] | str | bytes

__all__ = ["EgressLog", "install_no_egress"]


@dataclass
class EgressLog:
    """Every connect the guard saw: the loopback ones it let through, the others it refused."""

    allowed: list[str] = field(default_factory=list)  # "host:port" of each loopback connect.
    refused: list[str] = field(default_factory=list)  # "host:port" of each non-loopback attempt.

    def assert_clean_and_live(self) -> None:
        """Assert nothing left the machine and that the guard really watched some traffic."""
        assert not self.refused, f"egress to {self.refused}"
        assert self.allowed, "the guard saw no loopback connect: it was not live"


def install_no_egress(monkeypatch: pytest.MonkeyPatch) -> EgressLog:
    """Replace the connect entry points of `socket` with guarded wrappers; return their record.

    Args:
        monkeypatch: The test's own fixture, which undoes all three patches at teardown.

    Returns:
        The log every wrapper appends to.
    """
    log = EgressLog()
    connect = socket.socket.connect
    connect_ex = socket.socket.connect_ex
    create_connection = socket.create_connection

    def guarded_connect(self: socket.socket, address: _Address) -> None:
        _check(log, address)
        connect(self, address)

    def guarded_connect_ex(self: socket.socket, address: _Address) -> int:
        _check(log, address)
        return connect_ex(self, address)

    def guarded_create_connection(
        address: tuple[str | None, int], *args: object, **kwargs: object
    ) -> socket.socket:
        # Refused before the lookup it would otherwise start; the connect inside it is the one
        # that records an allowed address, so a loopback dial is counted once, not twice.
        _refuse_if_remote(log, address)
        return create_connection(address, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(socket.socket, "connect", guarded_connect)
    monkeypatch.setattr(socket.socket, "connect_ex", guarded_connect_ex)
    monkeypatch.setattr(socket, "create_connection", guarded_create_connection)
    return log


def _check(log: EgressLog, address: _Address) -> None:
    """Record `address` as allowed, or record and raise when it is not loopback."""
    _refuse_if_remote(log, address)
    if _is_network_address(address):
        log.allowed.append(_spelled(address))


def _refuse_if_remote(log: EgressLog, address: _Address) -> None:
    """Raise `AssertionError("egress to <address>")` for a network address off loopback."""
    if not _is_network_address(address) or is_loopback_host(str(address[0])):
        return
    log.refused.append(_spelled(address))
    raise AssertionError(f"egress to {_spelled(address)}")


def _is_network_address(address: _Address) -> bool:
    """True for a `(host, port, ...)` tuple; a Unix-domain path (a str or bytes) is not one."""
    return isinstance(address, tuple) and len(address) >= 2


def _spelled(address: _Address) -> str:
    """Render a network address as `host:port`."""
    return f"{address[0]}:{address[1]}"
