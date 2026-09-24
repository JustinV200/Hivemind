"""Read the committed Landing Board document: its operations, its streams and their schemas.

A third-party program knows the Landing Board (the Hive Entrance's versioned API, ADR-0034) only
through ``docs/entrance/openapi.json``. ``LandingBoard`` is that program's whole view of it: it
finds an operation by method and path template, fills the template's parameters (each checked
against its declared schema first), and validates a body against the schema the document declares
for a request, for a response of a given status, or for a stream's frame. A status the document
does not declare for an operation is itself a failure, because a client cannot act on it.

Fits into the Hive:
    Test infrastructure (codingrules section 14.5), not shipped. Used by
    ``e2e.landing_client.client``; calls into ``e2e.landing_client.schema``.

Key invariants:
    - Everything here comes from the document passed in; nothing is imported from the Hive.
    - An operation or stream the document does not describe is an error, never a guess.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote, urlencode

from e2e.landing_client.schema import SchemaError, validate

JSON_MEDIA_TYPE = "application/json"  # The one media type the document's bodies use.
_PARAMETER = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")  # A path template's parameter.

__all__ = ["JSON_MEDIA_TYPE", "LandingBoard", "Operation", "Stream", "as_object"]


def as_object(value: object, what: str = "value") -> dict[str, object]:
    """Narrow a decoded JSON value that must be an object.

    Args:
        value: The decoded value.
        what: What it is, for the error message.

    Returns:
        The same value, typed as a JSON object.

    Raises:
        SchemaError: The value is not an object.
    """
    if not isinstance(value, dict):
        raise SchemaError(f"{what} is not a JSON object: {value!r}")
    return value


@dataclass(frozen=True, slots=True)
class Operation:
    """One operation of the document: its method, its path template and its description."""

    method: str
    template: str
    spec: Mapping[str, object]

    @property
    def listeners(self) -> tuple[str, ...]:
        """The listeners that serve it (``x-hive-listeners``)."""
        declared = self.spec.get("x-hive-listeners")
        return tuple(str(name) for name in declared) if isinstance(declared, list) else ()

    @property
    def needs_session(self) -> bool:
        """Whether it names a security requirement (an empty list means none)."""
        security = self.spec.get("security")
        return isinstance(security, list) and bool(security)

    def declared(self, status: int) -> Mapping[str, object]:
        """Return the document's response for ``status``.

        Args:
            status: The HTTP status the Entrance answered.

        Returns:
            The response object (its description and, when it has a body, its content).

        Raises:
            SchemaError: The document declares no such status for this operation.
        """
        responses = as_object(self.spec.get("responses"), "responses")
        response = responses.get(str(status))
        if response is None:
            raise SchemaError(f"{self.method} {self.template} declares no {status} response")
        return as_object(response, "a response")


@dataclass(frozen=True, slots=True)
class Stream:
    """One WebSocket view of ``x-hive-streams``: its path, its first frame and its frames."""

    path: str
    spec: Mapping[str, object]

    @property
    def first_frame(self) -> Mapping[str, object]:
        """The first frame's description: its schema, its deadline and the string it signs."""
        return as_object(self.spec.get("first_frame"), "first_frame")

    @property
    def frame_schema(self) -> Mapping[str, object]:
        """The schema every frame the Entrance sends matches."""
        return as_object(self.spec.get("frame"), "frame")


class LandingBoard:
    """The committed OpenAPI document, as a generic client reads it."""

    def __init__(self, document: Mapping[str, object]) -> None:
        """Hold a parsed document.

        Args:
            document: The OpenAPI document, parsed from JSON.
        """
        self.document = document

    @classmethod
    def load(cls, path: Path) -> LandingBoard:
        """Read the document from a file.

        Args:
            path: ``docs/entrance/openapi.json``.

        Returns:
            The document, ready to query.
        """
        return cls(as_object(json.loads(path.read_text(encoding="utf-8")), "the document"))

    @property
    def signing(self) -> Mapping[str, object]:
        """The ``x-hive-signing`` extension: every signed string, header and encoding."""
        return as_object(self.document.get("x-hive-signing"), "x-hive-signing")

    def operation(self, method: str, template: str) -> Operation:
        """Find an operation by method and path template.

        Args:
            method: The HTTP method, any case.
            template: The path exactly as the document spells it, e.g. ``/v1/goals/{request_id}``.

        Returns:
            The operation.

        Raises:
            SchemaError: The document has no such operation.
        """
        item = as_object(self.document.get("paths"), "paths").get(template)
        spec = as_object(item, template).get(method.lower()) if item is not None else None
        if spec is None:
            raise SchemaError(f"the document describes no {method.upper()} {template}")
        return Operation(method.upper(), template, as_object(spec, template))

    def operations(self) -> Iterator[Operation]:
        """Yield every operation the document describes, in document order."""
        for template, item in as_object(self.document.get("paths"), "paths").items():
            for method, spec in as_object(item, template).items():
                yield Operation(method.upper(), template, as_object(spec, template))

    def stream(self, path: str) -> Stream:
        """Find a WebSocket view of ``x-hive-streams`` by its path.

        Args:
            path: The stream's path, e.g. ``/v1/push/stream``.

        Returns:
            The stream.

        Raises:
            SchemaError: The document describes no such stream.
        """
        streams = self.document.get("x-hive-streams")
        for entry in streams if isinstance(streams, list) else []:
            spec = as_object(entry, "a stream")
            if spec.get("path") == path:
                return Stream(path, spec)
        raise SchemaError(f"the document describes no stream at {path}")

    def check(self, value: object, schema: Mapping[str, object], what: str) -> None:
        """Validate ``value`` against ``schema``, resolving references in this document.

        Args:
            value: A decoded JSON value.
            schema: The schema the document declares for it.
            what: What the value is, for the error message.

        Raises:
            SchemaError: The value breaks the schema.
        """
        validate(value, schema, self.document, what)

    def target(
        self,
        operation: Operation,
        params: Mapping[str, str],
        query: Mapping[str, str | int],
    ) -> str:
        """Fill the operation's path template and query, each value checked against its schema.

        Args:
            operation: The operation being called.
            params: A value for every ``{name}`` in the template.
            query: Query parameters the operation declares; empty for none.

        Returns:
            The request target (path and query) exactly as it will be sent and signed.

        Raises:
            SchemaError: A parameter is missing, undeclared or breaks its schema.
        """
        declared = self._parameters(operation)

        def fill(match: re.Match[str]) -> str:
            """Check one path parameter's value and percent-encode it."""
            name = match.group(1)
            if name not in params or ("path", name) not in declared:
                raise SchemaError(f"{operation.template}: no declared value for {{{name}}}")
            self.check(params[name], declared[("path", name)], f"path parameter {name}")
            return quote(params[name], safe="")

        path = _PARAMETER.sub(fill, operation.template)
        for name, value in query.items():
            if ("query", name) not in declared:
                raise SchemaError(f"{operation.template} declares no query parameter {name}")
            self.check(value, declared[("query", name)], f"query parameter {name}")
        return f"{path}?{urlencode(query)}" if query else path

    def request_schema(self, operation: Operation) -> Mapping[str, object] | None:
        """Return the declared JSON request body schema, or None when the operation takes none.

        Args:
            operation: The operation being called.

        Returns:
            The body schema, or None.
        """
        body = operation.spec.get("requestBody")
        if body is None:
            return None
        content = as_object(as_object(body, "requestBody").get("content"), "content")
        return as_object(as_object(content.get(JSON_MEDIA_TYPE), "media").get("schema"), "schema")

    def _parameters(self, operation: Operation) -> dict[tuple[str, str], Mapping[str, object]]:
        """Index the operation's declared parameters by where they go and their name."""
        declared: dict[tuple[str, str], Mapping[str, object]] = {}
        parameters = operation.spec.get("parameters")
        for entry in parameters if isinstance(parameters, list) else []:
            parameter = as_object(entry, "a parameter")
            key = (str(parameter.get("in")), str(parameter.get("name")))
            declared[key] = as_object(parameter.get("schema"), "a parameter schema")
        return declared
