"""Tests for the transcription adapter over a real loopback socket, from binding to shutdown.

The other transcription tests run over `httpx.MockTransport`, which never opens a socket. This
one stands up a minimal HTTP/1.1 server on 127.0.0.1 speaking the transcription wire and drives
a clip through everything the Entrance will use -- `ProviderRegistry` (offline, since loopback is
local), `bind_transcriber`, the Fanner, `OpenAICompatTranscription` and httpx's real connection
pool -- then closes the registry and proves the pooled keep-alive connection was really closed:
the leak `ProviderRegistry.aclose` exists to prevent is only visible on a real socket.

Fits into the Hive:
    Mirrors src/hivemind/llm/providers/openai_compat/transcription.py (codingrules section 3),
    split from test_transcription.py by feature: the real transport rather than a mock one.
    Loopback only; no external network (like waggle's own websocket transport tests).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.llm.providers.openai_compat.transcription for the adapter under test.
    - hivemind.llm.registry for ProviderRegistry.transcriber and aclose.
"""

from __future__ import annotations

import asyncio
import email.policy
import json
from dataclasses import dataclass, field
from email.message import EmailMessage
from email.parser import BytesParser

from builders.audio import silent_wav

from hivemind.forage.map import ForageMap, SlotBinding
from hivemind.forage.slots import Effort
from hivemind.forage.tempo import Tempo
from hivemind.llm.capabilities import HealthState
from hivemind.llm.fanner import bind_transcriber
from hivemind.llm.fanner.lane import Fanner, FannerDeps
from hivemind.llm.fanner.recorder import TrailLlmEventRecorder
from hivemind.llm.registry import ProviderConfig, ProviderRegistry, RegistryDeps, default_factories
from hivemind.llm.transcription import AudioClip, wav_duration_s
from hivemind.pheromone import LlmEvent, MemoryPheromoneTrail, TrailQuery
from waggle.clock import FakeClock
from waggle.ids import new_hive_id, new_node_id

_WAIT_S = 5.0  # The longest any step of this loopback exchange may take before the test fails.
_WORDS = "Water the tomatoes at six."


@dataclass
class _Upload:
    """What the loopback server read out of one multipart transcription request."""

    fields: dict[str, str]
    filename: str | None
    content_type: str
    data: bytes


@dataclass
class _SpeechServer:
    """A minimal keep-alive HTTP/1.1 server speaking the transcription wire on loopback."""

    uploads: list[_Upload] = field(default_factory=list)
    open_connections: int = 0
    all_closed: asyncio.Event = field(default_factory=asyncio.Event)

    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        """Serve requests on one connection until the client closes it."""
        self.open_connections += 1
        self.all_closed.clear()
        # One connection carries many requests (keep-alive) until the client hangs up.
        while (request := await _read_request(reader)) is not None:
            path, headers, body = request
            status, answer = self._answer(path, headers, body)
            payload = json.dumps(answer).encode()
            writer.write(
                f"HTTP/1.1 {status} OK\r\nContent-Type: application/json\r\n"
                f"Content-Length: {len(payload)}\r\nConnection: keep-alive\r\n\r\n".encode()
                + payload
            )
            await writer.drain()
        writer.close()
        self.open_connections -= 1
        if self.open_connections == 0:
            self.all_closed.set()

    def _answer(self, path: str, headers: dict[str, str], body: bytes) -> tuple[int, object]:
        """Answer the model listing, or read an upload and answer it in verbose_json."""
        if path == "/v1/models":
            return 200, {"object": "list", "data": [{"id": "test-model"}]}
        upload = _parse_upload(headers["content-type"], body)
        self.uploads.append(upload)
        seconds = wav_duration_s(upload.data)  # Measured from the bytes that really arrived.
        segment = {"id": 0, "start": 0.0, "end": seconds, "text": f" {_WORDS}"}
        return 200, {
            "task": "transcribe",
            "language": "english",
            "duration": seconds,
            "text": f" {_WORDS}",
            "segments": [segment],
        }


async def _read_request(reader: asyncio.StreamReader) -> tuple[str, dict[str, str], bytes] | None:
    """Read one request (line, headers, Content-Length body); None when the client closed."""
    try:
        head = await reader.readuntil(b"\r\n\r\n")
    except asyncio.IncompleteReadError:
        return None  # EOF between requests: the client closed its pooled connection.
    request_line, *header_lines = head.decode("latin-1").split("\r\n")
    headers = {
        name.strip().lower(): value.strip()
        for name, _, value in (line.partition(":") for line in header_lines if line)
    }
    body = await reader.readexactly(int(headers.get("content-length", "0")))
    return request_line.split(" ")[1], headers, body


def _parse_upload(content_type: str, body: bytes) -> _Upload:
    """Parse a multipart/form-data body with the standard library's email parser."""
    message = BytesParser(policy=email.policy.HTTP).parsebytes(
        f"Content-Type: {content_type}\r\n\r\n".encode() + body
    )
    fields: dict[str, str] = {}
    upload = _Upload(fields=fields, filename=None, content_type="", data=b"")
    for part in message.iter_parts():
        assert isinstance(part, EmailMessage)
        name = part.get_param("name", header="content-disposition")
        payload = part.get_payload(decode=True)
        assert isinstance(payload, bytes)
        if part.get_filename() is None:
            fields[str(name)] = payload.decode()
            continue
        upload.filename = part.get_filename()
        upload.content_type = part.get_content_type()
        upload.data = payload
    return upload


async def _serve_until_eof(server: _SpeechServer) -> tuple[asyncio.Server, str]:
    """Start `server` on an ephemeral loopback port; return it and its base URL."""
    listener = await asyncio.start_server(server.handle, "127.0.0.1", 0)
    port = listener.sockets[0].getsockname()[1]
    return listener, f"http://127.0.0.1:{port}/v1"


async def test_a_clip_crosses_a_real_socket_and_the_registry_closes_the_pooled_connection() -> None:
    server = _SpeechServer()
    listener, base_url = await _serve_until_eof(server)
    clock = FakeClock()
    trail = MemoryPheromoneTrail(clock)
    recorder = TrailLlmEventRecorder(trail, new_hive_id(clock), new_node_id(clock), "human", clock)
    fanner = Fanner(
        FannerDeps(map=ForageMap([], clock), seats={}, limits={}, clock=clock, recorder=recorder)
    )
    # Offline on purpose: a speech server on loopback is exactly what an offline Hive may use.
    registry = ProviderRegistry(
        {"speech": ProviderConfig(kind="openai_compat", base_url=base_url, timeout_s=_WAIT_S)},
        [SlotBinding(key="transcriber", provider="speech", model="test-model", effort=Effort.LOW)],
        offline=True,
        deps=RegistryDeps(factories=default_factories(), environ={}, clock=clock),
    )
    clip = AudioClip.from_upload(silent_wav(1.5), "audio/x-wav")

    async with asyncio.timeout(_WAIT_S):
        transcriber = bind_transcriber(registry, fanner, Tempo())
        transcript = await transcriber.transcribe(clip, "en-US")
        health = await transcriber.health()
        pooled_before_close = server.open_connections
        await registry.aclose()
        await server.all_closed.wait()
    listener.close()
    await listener.wait_closed()

    (upload,) = server.uploads
    (event,) = [e for e in await trail.query(TrailQuery()) if isinstance(e, LlmEvent)]
    assert transcript.text == _WORDS
    assert transcript.language == "en"
    assert transcript.duration_s == 1.5
    assert upload.fields == {
        "model": "test-model",
        "response_format": "verbose_json",
        "language": "en",
    }
    assert (upload.filename, upload.content_type) == ("clip.wav", "audio/wav")
    assert upload.data == clip.data
    assert health.state is HealthState.HEALTHY
    assert (event.slot, event.provider, event.payload["audio_s"]) == ("TRANSCRIBER", "speech", 1.5)
    assert pooled_before_close == 1  # One keep-alive connection served both requests...
    assert server.open_connections == 0  # ...and aclose() really closed it.
