"""Drive the Landing Board as a third-party program would: from the committed document alone.

The Landing Board (the Hive Entrance's versioned HTTP and WebSocket API, ADR-0034) promises that a
program written from ``docs/entrance/openapi.json`` alone works. This package is that program, kept
honest by what it imports: the standard library, ``httpx``, ``websockets`` and ``cryptography``,
never ``hivemind``, ``waggle`` or the test builders (a test asserts it). ``LandingBoard`` reads the
document: it finds each operation by method and path, fills path templates, and validates bodies
against the declared schemas with ``validate`` (the JSON Schema subset the document uses, since
``jsonschema`` is not locked). ``SigningRules`` builds every signed string, header and first frame
from the document's ``x-hive-signing`` extension alone, after checking itself against the
document's own worked example. ``GenericClient`` makes the calls and opens the streams, and
refuses any status or body the document does not declare.

Fits into the Hive:
    Test infrastructure (codingrules section 14.5), not shipped. Used by the Landing Board
    conformance tests (roadmap step 10.5c).

Key invariants:
    - This file holds re-exports and ``__all__`` only.
    - Nothing in this package imports ``hivemind``, ``waggle`` or the test builders.

See Also:
    - docs/entrance/landing-board.md for the client guide this package follows.

Public API:
    - LandingBoard, Operation, Stream, as_object: the document.
    - SchemaError, validate: the schema subset.
    - DeviceKey, Session, SigningRules, b64url, sha256_hex: keys and signed strings.
    - GenericClient, Request, Prepared, Reply, StreamFeed: calls and streams.
"""

from e2e.landing_client.client import GenericClient, Prepared, Reply, Request, StreamFeed
from e2e.landing_client.document import LandingBoard, Operation, Stream, as_object
from e2e.landing_client.schema import SchemaError, validate
from e2e.landing_client.signing import DeviceKey, Session, SigningRules, b64url, sha256_hex

__all__ = [
    "DeviceKey",
    "GenericClient",
    "LandingBoard",
    "Operation",
    "Prepared",
    "Reply",
    "Request",
    "SchemaError",
    "Session",
    "SigningRules",
    "Stream",
    "StreamFeed",
    "as_object",
    "b64url",
    "sha256_hex",
    "validate",
]
