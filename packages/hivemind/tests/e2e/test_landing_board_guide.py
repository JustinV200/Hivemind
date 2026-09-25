"""End-to-end: the client guide's curl examples run, as written, against a live Hive Entrance.

Roadmap step 10.5c ships ``docs/entrance/landing-board.md`` with ``curl`` examples, and a signing
helper, ``docs/entrance/examples/hive-sign.sh``, because curl cannot sign. A guide that is only
written drifts; this test proves it. It takes every block the guide marks ``<!-- run: NAME -->``
and runs each named phase in a POSIX shell under ``sh -eu`` (the helper, OpenSSL 3, curl and jq),
in the order a client would, against ``hive serve``'s own composition over real loopback sockets
(``e2e.entrance_stand``), the operator approving at the Hive Stand between phases. The spoken goal
is a second of silence the Hive Stand's scripted transcriber hears as ``SPOKEN``; it is echoed back
held, and the operator declines it, since a program cannot confirm its own. The first frame the
helper prints opens the live push stream, which receives the Queen's question. The mutual-TLS
phases run against a Hive Stand whose remote listener demands client certificates (tunnel mode:
TLS on loopback, a throwaway authority's server certificate the client pins, a stand-in tunnel
client): the client makes its request, the operator registers and approves it with ``hive
entrance`` and writes its certificate, and the client is refused at the handshake without it and
logs in with it. Two static checks keep the guide honest: every ``curl`` example in it is one of
the phases run here, and every path and ``X-Hive-*`` header it names is in the committed OpenAPI
document.

Fits into the Hive:
    Test infrastructure (codingrules section 14.2), not shipped.

Key invariants:
    - None: this module holds tests only.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import subprocess
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from builders.audio import silent_wav
from builders.entrance.auth import PASSWORD
from builders.entrance.mtls import ServerTls, tunnel_manifest
from builders.entrance.stand import serving_stand, set_password
from e2e.entrance_stand import CHAT, QUESTION, REPLY, Stand, build_served, standing
from e2e.landing_client import LandingBoard, as_object
from websockets.asyncio.client import ClientConnection, connect

from hivemind.cli.compose.entrance import ServedHive
from hivemind.llm.transcription import FakeTranscription
from hivemind.manifest import HiveManifest

pytestmark = pytest.mark.e2e

ROOT = Path(__file__).resolve().parents[4]  # tests/e2e -> the checkout.
GUIDE = ROOT / "docs" / "entrance" / "landing-board.md"
HELPER = ROOT / "docs" / "entrance" / "examples" / "hive-sign.sh"
DOCUMENT = ROOT / "docs" / "entrance" / "openapi.json"
# The guide's runnable phases, in the order this test runs them.
PHASES = (
    "enrol",
    "login",
    "speak",
    "socket",
    "submit",
    "answer",
    "follow",
    "chat",
    "read-chat",
    "logout",
)
SPOKEN = "write a limerick about wasps"  # What the transcriber hears in the guide's clip.wav.
# The mutual-TLS phases, run against a listener that demands client certificates.
MTLS_PHASES = ("mtls-request", "mtls-login")
TOOLS = ("sh", "curl", "openssl", "jq")  # What the guide's examples need on the PATH.
PHASE_TIMEOUT_S = 60.0  # One phase's shell: a handful of local processes and loopback calls.
FRAME_TIMEOUT_S = 20.0  # The question notice arrives once the Drone asks: seconds, locally.
# Served by every listener but deliberately left out of the document it serves.
UNLISTED_PATHS = frozenset({"/v1/openapi.json"})
_MARKED = re.compile(r"<!-- run: ([a-z-]+) -->\n```sh\n(.*?)```", re.DOTALL)
_FENCED = re.compile(r"```sh\n(.*?)```", re.DOTALL)
_PATH = re.compile(r"/v1/[A-Za-z0-9_{}/.$()-]*")
_HEADER = re.compile(r"X-Hive-[A-Za-z-]+")


def _phases() -> dict[str, str]:
    """Each named phase of the guide: its marked blocks, in order, as one script."""
    scripts: dict[str, list[str]] = {}
    for name, block in _MARKED.findall(GUIDE.read_text(encoding="utf-8")):
        scripts.setdefault(name, []).append(block)
    return {name: "\n".join(blocks) for name, blocks in scripts.items()}


def _missing_tools() -> list[str]:
    """The tools the examples need that this host lacks (OpenSSL older than 3 counts)."""
    missing = [tool for tool in TOOLS if shutil.which(tool) is None]
    openssl = shutil.which("openssl")
    if openssl is not None:
        # SAFETY: a fixed argv naming the host's own openssl; nothing from outside reaches it.
        version = subprocess.run(  # noqa: S603
            [openssl, "version"], capture_output=True, text=True, timeout=10, check=False
        ).stdout
        if not version.startswith("OpenSSL 3"):
            missing.append("OpenSSL 3 (for pkeyutl -rawin)")
    return missing


def test_the_guides_curl_examples_run_against_a_live_entrance(tmp_path: Path) -> None:
    missing = _missing_tools()
    if missing:
        pytest.skip(f"the guide's examples need {', '.join(missing)}, absent on this host")
    manifest, served = build_served(tmp_path / "hive")
    workdir = tmp_path / "client"
    workdir.mkdir()

    outputs = asyncio.run(_walkthrough(manifest, served, workdir))

    enrolled, login, submitted = outputs["enrol"], outputs["login"], outputs["submit"]
    assert enrolled.splitlines()[-1] == "401"  # Pending: no login until approved.
    assert json.loads(login)["listener"] == "loopback"
    assert json.loads(outputs["speak"]) == {"transcript": SPOKEN, "state": "AWAITING_CONFIRMATION"}
    assert json.loads(submitted)["state"] == "RECEIVED"
    assert f": {QUESTION}" in outputs["answer"] and outputs["answer"].endswith("ANSWERED\n")
    followed = json.loads(outputs["follow"])
    assert followed["state"] == "PLANNED" and followed["finished_at"] is not None
    assert outputs["chat"].startswith("chat_")
    lines = outputs["read-chat"].splitlines()
    assert f"queen question: {QUESTION}" in lines
    assert lines[-2:] == [f"human message: {CHAT}", f"queen reply: {REPLY}"]
    assert outputs["logout"] == "204\n"
    assert outputs["notice"] == "question_waiting"


def test_the_guides_mutual_tls_examples_run_against_a_listener_demanding_certificates(
    tmp_path: Path,
) -> None:
    missing = _missing_tools()
    if missing:
        pytest.skip(f"the guide's examples need {', '.join(missing)}, absent on this host")
    workdir = tmp_path / "client"
    workdir.mkdir()

    outputs = asyncio.run(_mutual_tls(tmp_path / "stand", workdir))

    refused, logged_in = outputs["mtls-login"].split("\n", 1)
    assert refused.startswith("refused at the handshake (curl exit ")
    session = json.loads(logged_in)
    assert session["listener"] == "remote"
    assert session["device_id"] == outputs["registered"]


async def _mutual_tls(root: Path, workdir: Path) -> dict[str, str]:
    """Run the mutual-TLS phases, the operator registering and approving at the Hive Stand."""
    path, tls = tunnel_manifest(root)
    await set_password(path)
    outputs: dict[str, str] = {}
    async with serving_stand(path) as (stand, entrance):
        env = _remote_environment(tls, entrance.listeners.remote_port)
        outputs["mtls-request"] = await _run("mtls-request", env, workdir)
        # The operator's side, as the guide shows it: register, then approve and write it out.
        public_key = (workdir / "public_key_hex").read_text(encoding="utf-8").strip()
        registered = await stand.entrance(
            *("register", "--name", "garden-bot", "--public-key", public_key),
            *("--csr", str(workdir / "device.csr")),
        )
        assert registered.exit_code == 0, registered.output
        device_id = registered.output.split("Registered ")[1].split()[0]
        approved = await stand.entrance(
            *("approve", device_id, "--spend-cap", "5", "--yes"),
            *("--certificate-out", str(workdir / "device.crt")),
        )
        assert approved.exit_code == 0, approved.output
        outputs["registered"] = device_id
        outputs["mtls-login"] = await _run("mtls-login", env, workdir)
    return outputs


def _remote_environment(tls: ServerTls, port: int | None) -> dict[str, str]:
    """The guide's variables for the remote listener: its URL, the authority to pin, the rest."""
    assert port is not None, "the tunnel stand serves its remote listener"
    return {
        "PATH": os.environ.get("PATH", ""),
        "LC_ALL": "C",
        # By address: the listener's certificate also names 127.0.0.1, so no hosts file is touched.
        "HIVE_URL": f"https://127.0.0.1:{port}",
        "HIVE_CA": str(tls.ca_path),
        "HIVE_SIGN": str(HELPER),
        "HIVE_PASSWORD": PASSWORD,
    }


async def _walkthrough(manifest: HiveManifest, served: ServedHive, workdir: Path) -> dict[str, str]:
    """Run the guide's phases in order, the operator approving between them; collect output."""
    outputs: dict[str, str] = {}
    async with standing(manifest, served) as stand:
        env = _environment(stand, await stand.invite())
        outputs["enrol"] = await _run("enrol", env, workdir)
        device_id = (workdir / "device_id").read_text(encoding="utf-8").strip()
        await stand.approve(device_id)
        outputs["login"] = await _run("login", env, workdir)
        outputs["speak"] = await _speak(stand, env, workdir)
        hello = await _run("socket", env, workdir)
        async with _socket(stand, hello, device_id) as push:
            outputs["submit"] = await _run("submit", env, workdir)
            outputs["notice"] = await _notice(push)
            await stand.until(stand.question_waiting)
            outputs["answer"] = await _run("answer", env, workdir)
        request_id = (workdir / "goal_request").read_text(encoding="utf-8").strip()
        await stand.until(lambda: stand.goal_finished(request_id))
        outputs["follow"] = await _run("follow", env, workdir)
        outputs["chat"] = await _run("chat", env, workdir)
        await stand.until(stand.replied)
        outputs["read-chat"] = await _run("read-chat", env, workdir)
        outputs["logout"] = await _run("logout", env, workdir)
    return outputs


def _environment(stand: Stand, code: str) -> dict[str, str]:
    """The four variables the guide sets up, and a PATH; no proxy variable reaches curl."""
    return {
        "PATH": os.environ.get("PATH", ""),
        "LC_ALL": "C",
        "HIVE_URL": stand.loopback_url,
        "HIVE_SIGN": str(HELPER),
        "INVITE_CODE": code,
        "HIVE_PASSWORD": stand.password,
    }


async def _speak(stand: Stand, env: Mapping[str, str], workdir: Path) -> str:
    """Record the guide's ``clip.wav``, script what is heard, speak, then decline at the Stand."""
    # Cached per (provider, model): the very instance the Entrance's own Ears transcribe with.
    transcriber = stand.served.hive.registry.bound_transcriber().provider
    assert isinstance(transcriber, FakeTranscription)
    transcriber.script(SPOKEN)
    (workdir / "clip.wav").write_bytes(silent_wav(1.0))
    spoken = await _run("speak", env, workdir)
    # A person (the operator's console) declines the program's held goal: it never spends.
    target = f"/v1/goals/{(workdir / 'spoken_goal').read_text(encoding='utf-8').strip()}/decline"
    declined = await stand.console.call(stand.session, "POST", target, {"reason": "misheard"})
    assert declined.status_code == 200 and declined.json()["state"] == "REFUSED", declined.text
    return spoken


async def _run(phase: str, env: Mapping[str, str], workdir: Path) -> str:
    """Run one phase of the guide in ``sh -eu`` inside the client's directory; return stdout."""
    shell = shutil.which("sh")
    assert shell is not None
    # SAFETY: the script is the repository's own guide text, run as the guide tells a reader to,
    # with an environment built here; the Entrance it calls is this test's own, on loopback.
    process = await asyncio.create_subprocess_exec(
        shell,
        "-eu",
        "-c",
        _phases()[phase],
        cwd=workdir,
        env=dict(env),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        # External wait: local processes and loopback calls, seconds at most.
        async with asyncio.timeout(PHASE_TIMEOUT_S):
            out, err = await process.communicate()
    except TimeoutError:
        process.kill()
        await process.wait()
        raise
    assert process.returncode == 0, f"{phase} failed: {err.decode(errors='replace')}"
    # Universal newlines, as a text read gives: tools under Git for Windows' sh write CRLF.
    return out.decode().replace("\r\n", "\n")


@asynccontextmanager
async def _socket(stand: Stand, hello: str, device_id: str) -> AsyncIterator[ClientConnection]:
    """Open the live push stream with the helper's first frame; wait until it is attached."""
    board = LandingBoard.load(DOCUMENT)
    stream = board.stream("/v1/push/stream")
    board.check(json.loads(hello), as_object(stream.first_frame.get("schema")), "the first frame")
    url = stand.loopback_url.replace("http", "ws", 1) + stream.path
    async with connect(url, proxy=None, open_timeout=5.0, close_timeout=2.0) as socket:
        await socket.send(hello.strip())
        live = stand.entrance.services.push.live
        await stand.until(lambda: device_id in live.live_devices())
        yield socket


async def _notice(socket: ClientConnection, kind: str = "question_waiting") -> str:
    """Receive push frames, each checked against the document, until one of ``kind`` arrives."""
    board = LandingBoard.load(DOCUMENT)
    schema = board.stream("/v1/push/stream").frame_schema
    # External wait: the notice follows the Drone's question, seconds after the goal is taken.
    async with asyncio.timeout(FRAME_TIMEOUT_S):
        while True:
            frame = json.loads(await socket.recv())
            board.check(frame, schema, "a push frame")
            if frame["kind"] == kind:
                return str(frame["kind"])


def test_every_curl_example_in_the_guide_is_a_phase_this_test_runs() -> None:
    text = GUIDE.read_text(encoding="utf-8")
    marked = {block for _name, block in _MARKED.findall(text)}

    unrun = [block for block in _FENCED.findall(text) if "curl " in block and block not in marked]

    assert unrun == [], "every curl example must be marked <!-- run: NAME --> and run here"
    assert set(_phases()) == set(PHASES) | set(MTLS_PHASES)


def test_every_path_and_header_the_guide_names_is_in_the_document() -> None:
    text = GUIDE.read_text(encoding="utf-8")
    board = LandingBoard.load(DOCUMENT)
    templates = [operation.template for operation in board.operations()]
    streams = board.document.get("x-hive-streams")
    entries = streams if isinstance(streams, list) else []
    templates += [str(as_object(entry).get("path")) for entry in entries]
    signing = json.dumps(board.signing)

    named = {path.rstrip(".") for path in _PATH.findall(text)}
    unknown = [
        path
        for path in named
        if len(path.strip("/").split("/")) > 1
        and path not in UNLISTED_PATHS
        and not any(_same_route(path, template) for template in templates)
    ]
    headers = set(_HEADER.findall(text))

    assert unknown == [], f"the guide names paths the document lacks: {unknown}"
    assert headers and all(header in signing for header in headers), headers


def _same_route(named: str, template: str) -> bool:
    """Whether a path the guide names is ``template``, a placeholder or shell value per segment."""
    ours, theirs = named.strip("/").split("/"), template.strip("/").split("/")
    return len(ours) == len(theirs) and all(
        a == b or a.startswith(("{", "$")) or b.startswith("{")
        for a, b in zip(ours, theirs, strict=True)
    )
