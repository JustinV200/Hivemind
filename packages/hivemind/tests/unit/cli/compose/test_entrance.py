"""Test hivemind.cli.compose.entrance: `hive serve` starts the Entrance in every mode it can honour.

ADR-0033: loopback serves on its own; every remote mode serves TLS on a DNS name from the remote
listener, and lan and tunnel (and vpn when the operator turns it on) complete a handshake only with
a client certificate from the Hive's own authority. Each test composes the Hive `hive serve` runs
(the fake provider, the Hive's own SQLite file) and enters ``serve_hive`` on this host, with a
fake interface table where a mode reads interfaces: loopback fetches the contract on loopback;
vpn, lan and tunnel (a stub tunnel command standing in for the tunnel client) fetch it on the
remote listener over TLS, behind a self-signed certificate on a DNS name that the client pins and
verifies by that name, with a client certificate the Hive's authority issued exactly as approval
issues one, and without it. vpn and lan bind a private address this machine really has (the
kernel binds what the plan names; the fake table only has to agree). A loopback listener that
cannot bind refuses too. Every refusal of an exposure rule is in ``test_entrance_refusals``.

Fits into the Hive:
    Test infrastructure (codingrules section 14.2), not shipped.

Key invariants:
    - None: this module holds tests only.
"""

from __future__ import annotations

import asyncio
import json
import socket
import ssl
import sys
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from pathlib import Path

import httpx
import pytest
from builders.entrance.mtls import (
    PUBLIC_NAME,
    PrivateAddress,
    ServerTls,
    private_address,
    self_signed_tls,
    served_exposed,
)

from hivemind.cli.compose.entrance import ServedHive, serve_hive
from hivemind.cli.landing import ClientCertificate, certificate_request, present
from hivemind.common.secrets import FileSecretStore
from hivemind.entrance.app import OPENAPI_PATH
from hivemind.entrance.enrol import DeviceCertifier
from hivemind.entrance.expose import ExposurePlan, ListenerPlan, load_or_create_authority
from hivemind.manifest import EntranceExposure
from waggle.ids import DeviceId, IdKind, new_id
from waggle.signing import Ed25519Signer

_LOOPBACK_ONLY = {"lo": ["127.0.0.1"]}  # The host loopback mode never asks about.
# How a handshake the listener refused reaches an HTTP client: TLS 1.3 refuses a missing client
# certificate after the client's own side finished, so it shows as a dropped connection.
_HANDSHAKE_REFUSED = (httpx.ConnectError, httpx.ReadError, httpx.RemoteProtocolError)
# The tunnel client's stand-in: says it ran (a file), then idles until the Entrance ends it.
_TUNNEL_STUB = "import pathlib, sys, time; pathlib.Path(sys.argv[1]).touch(); time.sleep(3600)"
_REAL_WAIT_S = 20.0  # A child interpreter starts in tens of milliseconds; this is a hang.
_POLL_S = 0.01  # How often the test looks for the file the stub writes.

_needs_address = pytest.mark.skipif(
    private_address() is None, reason="this machine has no private IPv4 address to bind"
)


@dataclass(frozen=True, slots=True)
class _Answers:
    """The contract's status on the remote listener; None where the handshake was refused.

    Attributes:
        with_certificate: Presenting a client certificate the Hive's authority issued.
        without: Presenting none.
    """

    with_certificate: int | None
    without: int | None


async def _enter(served: ServedHive) -> int:
    """Enter serve_hive, fetch the contract on loopback, and return its status."""
    async with serve_hive(served) as entrance:
        url = f"http://localhost:{entrance.listeners.loopback_port}{OPENAPI_PATH}"
        async with httpx.AsyncClient(trust_env=False) as http:
            return (await http.get(url)).status_code


def test_loopback_serves_the_contract(tmp_path: Path) -> None:
    served = served_exposed(tmp_path, 'bind = "127.0.0.1:0"\n', _LOOPBACK_ONLY)

    status = asyncio.run(_enter(served))

    assert status == 200


async def _both(served: ServedHive) -> tuple[int, int]:
    """Enter serve_hive and fetch the contract from both listeners, plain HTTP."""
    async with serve_hive(served) as entrance, httpx.AsyncClient(trust_env=False) as http:
        listeners = entrance.listeners
        statuses = []
        for port in (listeners.loopback_port, listeners.remote_port):
            statuses.append((await http.get(f"http://127.0.0.1:{port}{OPENAPI_PATH}")).status_code)
        return statuses[0], statuses[1]


def test_an_injected_plan_is_served_as_given(tmp_path: Path) -> None:
    # A test's own plan (ServedHive.plan) opens a remote listener no manifest could ask for here.
    listener = ListenerPlan("127.0.0.1", 0, None)
    plan = ExposurePlan(
        mode=EntranceExposure.VPN,
        loopback=listener,
        remote=listener,
        public_origin="https://hive.example.ts.net",
        rp_id="hive.example.ts.net",
        tunnel_argv=(),
    )
    served = served_exposed(tmp_path, 'bind = "127.0.0.1:0"\n', _LOOPBACK_ONLY)

    statuses = asyncio.run(_both(replace(served, plan=plan)))

    assert statuses == (200, 200)


def test_a_loopback_listener_that_cannot_bind_refuses_to_start(tmp_path: Path) -> None:
    # Hold the port for the whole test, so the Entrance's own bind can only fail.
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as taken:
        taken.bind(("127.0.0.1", 0))
        taken.listen(1)
        port = taken.getsockname()[1]
        served = served_exposed(tmp_path, f'bind = "127.0.0.1:{port}"\n', _LOOPBACK_ONLY)

        with pytest.raises(OSError):
            asyncio.run(_enter(served))


@_needs_address
def test_vpn_serves_tls_on_its_dns_name_and_asks_no_client_certificate(tmp_path: Path) -> None:
    place, tls = _place(), self_signed_tls(tmp_path / "tls")
    # The fake table puts this machine's own address on Tailscale's interface.
    values = {
        "expose": '"vpn"',
        "remote_bind": f'"{place.address}:0"',
        "vpn_cidrs": f'["{place.address}/32"]',
    }
    served = served_exposed(
        tmp_path / "hive", _section(tls, values), {"tailscale0": [place.address]}
    )

    answers = asyncio.run(_ask(served, tls, place.address))

    # vpn leaves mutual TLS off unless the operator turns it on: both are served.
    assert answers == _Answers(with_certificate=200, without=200)


@_needs_address
def test_lan_serves_tls_only_to_a_client_certificate_from_the_hive(tmp_path: Path) -> None:
    place, tls = _place(), self_signed_tls(tmp_path / "tls")
    values = {"expose": '"lan"', "remote_bind": f'"{place.address}:0"'}
    served = served_exposed(tmp_path / "hive", _section(tls, values), {"eth0": [place.address]})

    answers = asyncio.run(_ask(served, tls, place.address))

    assert answers == _Answers(with_certificate=200, without=None)


def test_tunnel_runs_its_client_and_serves_tls_only_to_a_client_certificate(
    tmp_path: Path,
) -> None:
    tls, ran = self_signed_tls(tmp_path / "tls"), tmp_path / "tunnel-ran"
    argv = json.dumps([sys.executable, "-c", _TUNNEL_STUB, str(ran)])
    values = {"expose": '"tunnel"', "remote_bind": '"127.0.0.1:0"', "tunnel_command": argv}
    served = served_exposed(tmp_path / "hive", _section(tls, values), _LOOPBACK_ONLY)

    answers = asyncio.run(_ask(served, tls, "127.0.0.1", ran))

    assert answers == _Answers(with_certificate=200, without=None)
    assert ran.exists()


def _place() -> PrivateAddress:
    """The private address vpn and lan bind (their tests are skipped without one)."""
    place = private_address()
    assert place is not None
    return place


def _section(tls: ServerTls, values: Mapping[str, str]) -> str:
    """A remote mode's ``[entrance]`` body: its values, public_url, and the TLS table last."""
    lines = ['bind = "127.0.0.1:0"', f'public_url = "https://{PUBLIC_NAME}"']
    lines += [f"{key} = {value}" for key, value in values.items()]
    lines += ["", "[entrance.tls]", f'cert = "{tls.cert_path}"', f'key = "{tls.key_path}"']
    return "\n".join(lines) + "\n"


async def _ask(
    served: ServedHive, tls: ServerTls, host: str, tunnel_ran: Path | None = None
) -> _Answers:
    """Serve, then fetch the contract on the remote listener with and without a certificate."""
    async with serve_hive(served) as entrance:
        port = entrance.listeners.remote_port
        assert port is not None, "a remote mode serves its remote listener"
        origin = f"https://{host}:{port}"
        device = await _issued(served)
        answers = _Answers(
            with_certificate=await _fetch(origin, _client(tls, device)),
            without=await _fetch(origin, _client(tls, None)),
        )
        # The tunnel client is the Entrance's child: it must have been started while serving.
        if tunnel_ran is not None:
            await _until(tunnel_ran.exists)
    return answers


async def _issued(served: ServedHive) -> ClientCertificate:
    """A device's client certificate, issued by the Hive's authority as approval issues one."""
    manifest, clock = served.hive.manifest, served.hive.clock
    store = FileSecretStore(manifest.resolve_path(manifest.hive.secrets_dir))
    # The authority serve_hive created in the Hive's secret store: loading it again finds it.
    authority = await load_or_create_authority(store, manifest.hive.id, clock.now())
    signer = Ed25519Signer.generate()
    device_id = DeviceId(new_id(IdKind.DEVICE, clock))
    request = certificate_request(signer, "compose test device")
    record = DeviceCertifier(authority).certify(device_id, request, clock.now())
    return ClientCertificate(record.pem.encode("ascii"), signer)


def _client(tls: ServerTls, device: ClientCertificate | None) -> ssl.SSLContext:
    """A client context pinning the self-signed certificate, presenting ``device``'s if given."""
    context = ssl.create_default_context(cafile=tls.ca_path)
    if device is not None:
        present(context, device)
    return context


async def _fetch(origin: str, context: ssl.SSLContext) -> int | None:
    """GET the contract at ``origin``; None when the listener refused the handshake."""
    # Reached by address, verified by name: the certificate names the DNS name alone.
    async with httpx.AsyncClient(verify=context, trust_env=False) as http:
        try:
            response = await http.get(
                f"{origin}{OPENAPI_PATH}", extensions={"sni_hostname": PUBLIC_NAME}
            )
        except _HANDSHAKE_REFUSED:
            return None
    return response.status_code


async def _until(predicate: Callable[[], bool]) -> None:
    """Poll ``predicate`` until it holds, failing after ``_REAL_WAIT_S``.

    The stub is a real process, whose start cannot run on a FakeClock (codingrules 14.5's "no
    sleeping in tests" is for logical waits; this coordinates with a real child).
    """
    deadline = time.monotonic() + _REAL_WAIT_S
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError(f"The condition did not hold within {_REAL_WAIT_S}s.")
        await asyncio.sleep(_POLL_S)
