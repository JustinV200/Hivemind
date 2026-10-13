# pollen tests

Tests for the pollen package: the Pollen Packet, the thin gateway program that runs on a device the
Hive borrows as a Real Cell. The tree mirrors `packages/pollen/src/pollen/`: one test module per
source module (`agent/test_packet.py` tests `agent/packet.py`), none over 400 lines, importing only
pollen, waggle and third-party libraries.

- `conftest.py` is the test composition root: a clock whose sleeps are cut short, a scripted Queen,
  an enrolled device and a running packet with one lease open.
- `support/` holds the helpers, on the test path under their own names: `queen_double.py` is a real
  loopback WebSocket listener that speaks Waggle as the Hive does (optionally over TLS with a
  pinned self-signed certificate), `device_kit.py` writes what an enrolment leaves and runs a
  packet on it, `scenarios.py` speaks the Warden's half of a conversation (open a lease, run, put,
  get, release), and `executor_kit.py` and `states.py` build the collaborators the unit tests need
  without a packet.
- Tests prefer a real packet against the scripted Queen, running real programs, to a mock; a fake
  is used only where the real thing cannot be made to fail (a table that cannot be written, an
  answer too big for one frame).
- The suite is deterministic and runs in well under a minute; run it from the repository root:
  `uv run pytest -q packages/pollen/tests`.
