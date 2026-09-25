# Hive Entrance docs

The Landing Board is the public contract of the Hive Entrance, the Hive's two-listener gateway.
The loopback listener is always on; the remote listener runs only when the Entrance is explicitly
exposed. This directory holds the contract, its client guide, and the signing helper the guide
uses.

- [`landing-board.md`](landing-board.md) is the client guide, for anyone writing a program against
  the Landing Board. Start here. It covers the two listeners, enrolment from the device's side,
  login, signing every request, step-up and held requests, and the three calls (submit a goal,
  subscribe, answer). It also covers the push contract, errors and refusals, and the versioning
  rule. Every step has a `curl` example. It also states the rule that nothing may forward TCP into
  the loopback listener.
- [`openapi.json`](openapi.json) is the committed OpenAPI document. It is generated from the
  Entrance's route models by `scripts/write_landing_board.py`, and CI fails when it differs from
  what the routes render. Never edit it by hand.
- [`examples/hive-sign.sh`](examples/hive-sign.sh) signs what the Landing Board asks a device to
  sign, because `curl` cannot. That covers enrolment, login, every request's three `X-Hive-*`
  headers and a WebSocket's first frame. It needs OpenSSL 3 and POSIX tools only.

The guide is tested, not just written. `packages/hivemind/tests/e2e/test_landing_board_guide.py`
runs every `curl` example in it against a live Entrance.
`packages/hivemind/tests/e2e/test_landing_board_conformance.py` drives the Entrance with a client
that reads nothing but `openapi.json`.
