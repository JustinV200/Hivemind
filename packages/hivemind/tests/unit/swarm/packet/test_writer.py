"""Tests for hivemind.swarm.packet.writer: which advertised URLs may reach an installer script.

Fits into the Hive:
    Mirrors src/hivemind/swarm/packet/writer.py (codingrules section 3). `HiveTarget.from_url` is
    the trust boundary between the manifest and the text of `pollinate.ps1` / `pollinate.sh`: a
    host it accepts is embedded in a script, so the table covers what a device can reach and what
    could break out of the script's quoting.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.swarm.packet.writer for the module under test.
"""

from __future__ import annotations

import pytest

from hivemind.swarm.errors import PacketError
from hivemind.swarm.packet import HiveTarget


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        (
            "wss://hivestand.example-tailnet.ts.net:8720",
            HiveTarget("hivestand.example-tailnet.ts.net", 8720),
        ),
        ("wss://100.101.102.103:8720", HiveTarget("100.101.102.103", 8720)),
        ("wss://[fd7a:115c:a1e0::1]:8720", HiveTarget("fd7a:115c:a1e0::1", 8720)),
        ("ws://hivestand.example-tailnet.ts.net:8720", None),  # Plaintext.
        ("wss://[fe80::1%25';calc;']:8720", None),  # A scope id carrying a script breakout.
        ("wss://127.0.0.1:8720", None),  # Loopback: the device's own machine.
        ("wss://0.0.0.0:8720", None),  # Unspecified: no machine at all.
        ("wss://" + "a" * 40 + "-:8720", None),  # A label ending in `-`, answered in linear time.
    ],
)
def test_from_url_accepts_reachable_hosts_and_refuses_unsafe_ones(
    url: str, expected: HiveTarget | None
) -> None:
    if expected is not None:
        assert HiveTarget.from_url(url) == expected
        return

    with pytest.raises(PacketError):
        HiveTarget.from_url(url)
