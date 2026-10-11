"""The no-egress guard refuses a remote connect and lets loopback through: the proof's own test.

An offline proof that its guard silently let everything through would prove nothing, so this pins
the guard's behaviour on its own: a connect to an address off loopback raises `AssertionError`
naming it (through `connect`, `connect_ex` and `create_connection` alike) and is recorded, while a
connect to a listener on `127.0.0.1` goes through and is recorded as allowed.

Fits into the Hive:
    Test infrastructure (codingrules section 14.2), not shipped.

Key invariants:
    - The remote address is a documentation-range one (RFC 5737), and the guard raises before any
      packet would be sent, so this test never touches a network.

See Also:
    - tests/e2e/offline/guard.py for the guard under test.
"""

from __future__ import annotations

import socket

import pytest
from e2e.offline import EgressLog

pytestmark = pytest.mark.e2e

_REMOTE = ("203.0.113.7", 9)  # RFC 5737 TEST-NET-3: documentation addresses, never routed.


@pytest.mark.parametrize("how", ["connect", "connect_ex", "create_connection"])
def test_a_connect_to_a_remote_address_is_refused_and_recorded(egress: EgressLog, how: str) -> None:
    with (
        socket.socket() as sock,
        pytest.raises(AssertionError, match=r"egress to 203\.0\.113\.7:9"),
    ):
        if how == "create_connection":
            socket.create_connection(_REMOTE)
        else:
            getattr(sock, how)(_REMOTE)

    assert egress.refused[-1] == "203.0.113.7:9"
    egress.refused.clear()  # Expected here; the fixture's teardown must not fail the test for it.


def test_a_connect_to_a_loopback_listener_goes_through_and_is_recorded(egress: EgressLog) -> None:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        port = listener.getsockname()[1]

        with socket.create_connection(("127.0.0.1", port)):
            pass

    assert egress.allowed == [f"127.0.0.1:{port}"]
    egress.assert_clean_and_live()
