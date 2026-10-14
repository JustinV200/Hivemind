"""Tests for hivemind.hive.night_veil.judges.hidden_service: judging a plain GET's HTTP status.

The old check passed on a `2` anywhere in curl's output, which let `502` and `521` through and
passed the listener's own `426` by accident. The judge now needs a clean exit and exactly the
expected, well-formed status, and the constant it expects is checked against a real Waggle listener.

Fits into the Hive:
    Mirrors src/hivemind/hive/night_veil/judges/hidden_service.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.hive.night_veil.judges.hidden_service for the module under test.
    - waggle.transport.websocket_server for the listener whose answer is 426.
"""

from __future__ import annotations

import httpx
import pytest

from hivemind.cell import CompletedCommand
from hivemind.hive.night_veil.judges import WEBSOCKET_PROBE_STATUS, judge_http_status
from hivemind.hive.night_veil.results import CheckStatus
from waggle.codec import Codec
from waggle.transport.websocket_server import WebSocketServer

_OK = "answered"


def _curl(exit_code: int, printed: bytes) -> CompletedCommand:
    return CompletedCommand(exit_code=exit_code, stdout=printed, stderr=b"", duration_s=0.01)


def _judge(completed: CompletedCommand) -> tuple[CheckStatus, str]:
    result = judge_http_status(completed, expected=WEBSOCKET_PROBE_STATUS, ok_detail=_OK)
    return result.status, result.detail


def test_the_websocket_endpoints_own_answer_is_green() -> None:
    assert _judge(_curl(0, b"426")) == (CheckStatus.PASS, _OK)


def test_the_status_may_be_followed_by_a_newline() -> None:
    assert _judge(_curl(0, b"426\n"))[0] is CheckStatus.PASS


@pytest.mark.parametrize("printed", [b"200", b"301", b"404", b"500", b"502", b"521", b"101"])
def test_any_other_well_formed_status_is_a_different_service_and_red(printed: bytes) -> None:
    # 200, 502 and 521 each carry a `2`, which is all the old check looked for.
    status, detail = _judge(_curl(0, printed))

    assert status is CheckStatus.FAIL
    assert detail == f"http_status={printed.decode()}"


@pytest.mark.parametrize(
    "printed", [b"", b"000", b"42", b"4260", b"x426", b"426 Upgrade Required", b"4 2 6", b"2xx"]
)
def test_output_that_is_not_one_status_code_is_red(printed: bytes) -> None:
    # curl prints 000 when no HTTP response arrived; anything else here is not a status at all.
    assert _judge(_curl(0, printed)) == (CheckStatus.FAIL, "no_http_status")


def test_a_failed_curl_is_red_whatever_it_printed() -> None:
    # Exit 97 is curl's SOCKS5 proxy handshake error: Tor could not reach the onion service.
    assert _judge(_curl(97, b"426")) == (CheckStatus.FAIL, "exit_code=97")


async def test_the_expected_status_is_what_a_real_waggle_listener_answers_a_plain_get_with() -> (
    None
):
    server = WebSocketServer(Codec())
    await server.start()
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(f"http://127.0.0.1:{server.port}/")
    finally:
        await server.close()

    assert response.status_code == WEBSOCKET_PROBE_STATUS == 426
