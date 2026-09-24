"""Call the Landing Board and open its streams as a program written from the document alone.

``GenericClient`` is the conformance client's hands (roadmap step 10.5c): it prepares a request
from an operation of the committed document (the target filled from the path template, the body
checked against the declared request schema, the four signed headers built by ``SigningRules`` when
the operation names a security requirement), sends it over ``httpx``, and accepts the reply only
when its status is one the document declares for that operation and its body matches the declared
schema for that status. ``stream`` opens one of the document's WebSocket views with ``websockets``,
sends the signed first frame (itself checked against the declared first-frame schema), and yields
a ``StreamFeed`` whose every frame is checked against the view's declared frame schema. A test
that needs a refused request (a bad signature, a replayed nonce, a stale timestamp) prepares one,
changes it, and sends it through the same checks.

Fits into the Hive:
    Test infrastructure (codingrules section 14.5), not shipped. Used by the Landing Board
    conformance tests; calls into ``httpx``, ``websockets`` and this package's document and
    signing modules.

Key invariants:
    - No reply reaches a test unless its status is declared and its body matches the document.
    - Every socket is closed when its ``stream`` block exits.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from urllib.parse import urlencode

import httpx
from e2e.landing_client.document import (
    JSON_MEDIA_TYPE,
    LandingBoard,
    Operation,
    Stream,
    as_object,
)
from e2e.landing_client.schema import SchemaError
from e2e.landing_client.signing import Session, SigningRules
from websockets.asyncio.client import ClientConnection, connect

SOCKET_OPEN_TIMEOUT_S = 5.0  # A loopback WebSocket handshake takes milliseconds.
SOCKET_CLOSE_TIMEOUT_S = 2.0  # The close handshake on exit, bounded like every external wait.
FRAME_TIMEOUT_S = 20.0  # The longest a test waits for one frame: a goal's whole run, locally.

__all__ = ["GenericClient", "Prepared", "Reply", "Request", "StreamFeed"]


@dataclass(frozen=True, slots=True)
class Request:
    """One call, in the document's own terms.

    Attributes:
        method: The HTTP method.
        template: The path template exactly as the document spells it.
        params: A value for every ``{name}`` in the template.
        query: Query parameters the operation declares.
        body: The JSON body, or None for no body.
    """

    method: str
    template: str
    params: Mapping[str, str] = field(default_factory=dict)
    query: Mapping[str, str | int] = field(default_factory=dict)
    body: object = None


@dataclass(frozen=True, slots=True)
class Prepared:
    """A request ready to send: the operation, the exact target, bytes and headers."""

    operation: Operation
    target: str
    content: bytes = field(repr=False)
    headers: Mapping[str, str] = field(repr=False)


@dataclass(frozen=True, slots=True)
class Reply:
    """A reply the document declares: its status and its decoded, validated body."""

    status: int
    body: object

    @property
    def data(self) -> dict[str, object]:
        """The body as a JSON object (every body the document declares is one)."""
        return as_object(self.body, f"a {self.status} body")


class GenericClient:
    """One device's calls and streams against one listener, checked against the document."""

    def __init__(self, http: httpx.AsyncClient, board: LandingBoard) -> None:
        """Hold the HTTP client pointed at a listener, and the document.

        Args:
            http: A client whose base URL is the listener; the caller closes it.
            board: The committed document.
        """
        self.http = http
        self.board = board
        self.rules = SigningRules(board.signing)

    def prepare(self, request: Request, session: Session | None = None) -> Prepared:
        """Build a request's target, body bytes and headers from the document.

        Args:
            request: The call, in the document's terms.
            session: The session to sign with; required when the operation needs one.

        Returns:
            The request, ready to send.

        Raises:
            SchemaError: The operation is not in the document, a parameter or the body breaks
                its declared schema, or a session is missing.
        """
        operation = self.board.operation(request.method, request.template)
        target = self.board.target(operation, request.params, request.query)
        schema = self.board.request_schema(operation)
        headers: dict[str, str] = {}
        content = b""
        # A body goes out only where the document declares one, and only once it validates.
        if request.body is not None:
            if schema is None:
                raise SchemaError(f"{operation.template} declares no request body")
            self.board.check(request.body, schema, "the request body")
            content = json.dumps(request.body).encode("utf-8")
            headers["Content-Type"] = JSON_MEDIA_TYPE
        # Only an operation that names HiveSession is signed: a public one carries no credential.
        if operation.needs_session:
            if session is None:
                raise SchemaError(f"{operation.template} needs a session")
            headers |= self.rules.request_headers(session, operation.method, target, content)
        return Prepared(operation, target, content, headers)

    async def send(self, prepared: Prepared) -> Reply:
        """Send a prepared request and accept only a reply the document declares.

        Args:
            prepared: What ``prepare`` built, possibly changed by a test.

        Returns:
            The status and the validated body.

        Raises:
            SchemaError: The status is undeclared, or the body breaks the declared schema.
        """
        operation = prepared.operation
        # External await: a loopback round trip; the client's own timeout bounds it.
        response = await self.http.request(
            operation.method, prepared.target, content=prepared.content, headers=prepared.headers
        )
        declared = operation.declared(response.status_code)
        what = f"{operation.method} {operation.template} {response.status_code}"
        content = declared.get("content")
        # A response the document gives no content (a 204) must come back empty.
        if content is None:
            if response.content:
                raise SchemaError(f"{what}: a body where the document declares none")
            return Reply(response.status_code, None)
        media = as_object(as_object(content, "content").get(JSON_MEDIA_TYPE), "media")
        body = response.json()
        self.board.check(body, as_object(media.get("schema"), "schema"), what)
        return Reply(response.status_code, body)

    async def call(self, request: Request, session: Session | None = None) -> Reply:
        """Prepare and send one call.

        Args:
            request: The call, in the document's terms.
            session: The session to sign with, when the operation needs one.

        Returns:
            The status and the validated body.
        """
        return await self.send(self.prepare(request, session))

    @asynccontextmanager
    async def stream(
        self, path: str, session: Session, query: Mapping[str, str] | None = None
    ) -> AsyncIterator[StreamFeed]:
        """Open one of the document's WebSocket views and authenticate it with its first frame.

        Args:
            path: The stream's path, as ``x-hive-streams`` names it.
            session: The session the socket belongs to.
            query: Query parameters (a stream's cursor), signed as part of the target.

        Yields:
            The feed of validated frames; the socket closes when the block exits.
        """
        # The first frame signs the target exactly as requested, and is checked before it is sent.
        stream = self.board.stream(path)
        target = f"{path}?{urlencode(query)}" if query else path
        tag = str(stream.first_frame.get("signs"))
        hello = self.rules.socket_hello(tag, session, target)
        self.board.check(hello, as_object(stream.first_frame.get("schema")), "the first frame")
        base = str(self.http.base_url).rstrip("/").replace("http", "ws", 1)
        # No proxy: the listener is on this host, and the environment may name one.
        async with connect(
            base + target,
            proxy=None,
            open_timeout=SOCKET_OPEN_TIMEOUT_S,
            close_timeout=SOCKET_CLOSE_TIMEOUT_S,
        ) as socket:
            await socket.send(json.dumps(hello))
            yield StreamFeed(socket, stream, self.board)


class StreamFeed:
    """The frames of one open stream, each checked against the view's declared frame schema."""

    def __init__(self, socket: ClientConnection, stream: Stream, board: LandingBoard) -> None:
        """Hold an authenticated socket.

        Args:
            socket: The open socket, its first frame sent.
            stream: The view it is on.
            board: The document, for the frame schema.
        """
        self.socket = socket
        self._stream = stream
        self._board = board
        # Frames received while waiting for another: notices of different refs may interleave.
        self._held: list[dict[str, object]] = []

    async def next(self, timeout_s: float = FRAME_TIMEOUT_S) -> dict[str, object]:
        """Receive the next frame from the socket and validate it.

        Args:
            timeout_s: The longest to wait for it.

        Returns:
            The frame, a JSON object.

        Raises:
            TimeoutError: No frame arrived in time.
            SchemaError: The frame breaks the declared frame schema.
        """
        # External await: the Entrance sends a frame when something happens; bounded.
        async with asyncio.timeout(timeout_s):
            raw = await self.socket.recv()
        frame = json.loads(raw)
        self._board.check(frame, self._stream.frame_schema, f"a {self._stream.path} frame")
        return as_object(frame, "a frame")

    async def until(self, kind: str, ref: str | None = None) -> dict[str, object]:
        """Return the first frame of ``kind`` (pointing at ``ref``, when given), held or new.

        Args:
            kind: The ``kind`` member to wait for, e.g. ``question_waiting``.
            ref: The ``ref`` it must point at; any when None.

        Returns:
            The matching frame; frames that do not match are held for a later wait.

        Raises:
            TimeoutError: No matching frame arrived within ``FRAME_TIMEOUT_S``.
        """

        def matches(frame: dict[str, object]) -> bool:
            """Whether ``frame`` is the one being waited for."""
            return frame.get("kind") == kind and (ref is None or frame.get("ref") == ref)

        for held in self._held:
            if matches(held):
                self._held.remove(held)
                return held
        # One deadline for the whole wait, however many other frames arrive first.
        async with asyncio.timeout(FRAME_TIMEOUT_S):
            while True:
                frame = await self.next()
                if matches(frame):
                    return frame
                self._held.append(frame)
