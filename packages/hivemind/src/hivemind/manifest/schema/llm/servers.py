"""Define ServerSpec, the ``[llm.servers.<server_id>]`` table: a model server a Cell may host.

A **model server** is a long-running OpenAI-compatible HTTP process (llama.cpp's server, vLLM,
Ollama, LM Studio) that a Cell's Warden (its always-on supervisor) starts on its own Cell, waits
until it answers its health probe, and then reports to the Queen (the central orchestrator) as new
Forage map sources (a Forage source is one model that can serve a call). The manifest, never code,
names the program: ``argv`` is the command that starts the server, with two literal placeholders
the Warden substitutes at start time (``{port}`` for ``port`` and ``{scratch}`` for the session's
scratch directory), so no server program, model file or port ever appears in source
(``scripts/check_no_model_ids.py``, codingrules section 8.6). ``provider`` names the
``[llm.providers]`` row the server answers as, and ``sources`` lists the ``[forage.map]`` entries
it may load, so the Hive Manifest alone says which models a Cell can offer. Two checks live here
because they span tables: ``check_servers`` (run by ``LlmSection``) requires each server's provider
to exist, to be ``openai_compat`` and to sit on a loopback ``base_url`` carrying the server's own
port, and ``check_server_sources`` (run by ``HiveManifest``, the one object that sees both
``[llm]`` and ``[forage]``) requires each source id to name a map entry whose provider is the
server's provider.

Fits into the Hive:
    Layer 1 (foundational services; capacity as data), inside the ``llm`` sub-package of the
    manifest schema. Embedded by ``hivemind.manifest.schema.llm.section.LlmSection``; its
    cross-table check is called by ``hivemind.manifest.schema.manifest.HiveManifest``; the
    composition root (``hivemind.cli.stores.server_configs``) turns each row into the Warden's own
    ``ServerConfig``. Calls into ``hivemind.manifest.schema.llm.providers``, ``waggle`` and the
    standard library only.

Key invariants:
    - A server's provider is ``openai_compat`` on a loopback ``base_url`` whose port equals the
      server's ``port``: the Warden reaches its own server over the Cell's loopback and nothing
      else, so a server can never be pointed at a hosted API or a remote host.
    - ``argv`` may carry only the placeholders ``{port}`` and ``{scratch}``; any other braced word
      is a typo caught at load time rather than a literal argument the server would choke on.
    - Every ``sources`` entry names a ``[forage.map]`` row of the server's own provider.

See Also:
    - docs/adr/0044-local-model-serving-via-openai-compatible-api.md for the decision.
    - hivemind.wardens.local_pool.hosting for the Warden code that starts what this describes.
    - hivemind.manifest.schema.llm.section for ``LlmSection.servers`` and its validator.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Annotated, Protocol
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator

from hivemind.manifest.schema.llm.providers import MANIFEST_KEY_PATTERN
from waggle.messages.forage.values import MAX_PROVIDER_CHARS
from waggle.uris import is_loopback_host

MAX_PORT = 65_535  # The highest TCP port; 0 would mean "pick one", which a fixed base_url forbids.
DEFAULT_STARTUP_TIMEOUT_S = 60.0  # A cold model load is slow; a minute fails loud, not hung.
DEFAULT_HEALTH_PATH = "/models"  # Relative to the provider's base_url, as the adapter probes it.
SERVED_KIND = "openai_compat"  # The one adapter every local model server is reached through.
MAX_SERVER_SOURCES = 32  # More models than one server loads at once is a typo, not a plan.
# The placeholders the Warden substitutes into argv at start time; any other braced word is wrong.
ARGV_PLACEHOLDERS = frozenset({"{port}", "{scratch}"})
_BRACED_WORD = re.compile(r"\{[A-Za-z0-9_]*\}")

__all__ = [
    "ARGV_PLACEHOLDERS",
    "DEFAULT_HEALTH_PATH",
    "DEFAULT_STARTUP_TIMEOUT_S",
    "SERVED_KIND",
    "ServerSpec",
    "check_server_sources",
    "check_servers",
]

# The manifest-key alias for a source id in `sources`: the same shape every [forage.map] key has.
_SourceKey = Annotated[str, Field(pattern=MANIFEST_KEY_PATTERN, max_length=64)]


class _ProviderRow(Protocol):
    """The two fields of a ``[llm.providers]`` row ``check_servers`` reads."""

    @property
    def kind(self) -> str:
        """The adapter kind."""
        ...

    @property
    def base_url(self) -> str:
        """The provider's base URL; empty for a hosted API."""
        ...


class _MapRow(Protocol):
    """The one field of a ``[forage.map]`` row ``check_server_sources`` reads."""

    @property
    def provider(self) -> str:
        """The ``[llm.providers]`` name the source is served by."""
        ...


class ServerSpec(BaseModel):
    """``[llm.servers.<server_id>]``: how a Cell's Warden starts one OpenAI-compatible server."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    provider: str = Field(
        max_length=MAX_PROVIDER_CHARS,
        description="The [llm.providers.<name>] row this server answers as; it must be "
        "openai_compat on a loopback base_url carrying this server's port.",
    )
    argv: tuple[str, ...] = Field(
        min_length=1,
        description="The command that starts the server on the Cell, never run through a shell. "
        "The literal {port} and {scratch} placeholders are substituted at start time; the "
        "program name lives here, never in code.",
    )
    port: int = Field(ge=1, le=MAX_PORT, description="The loopback TCP port the server listens on.")
    health_path: str = Field(
        default=DEFAULT_HEALTH_PATH,
        description="The path, below the provider's base_url, that answers once the server is "
        "ready. The OpenAI-compatible adapter's own health probe asks for /models, so a server "
        "whose readiness lives elsewhere must also serve /models.",
    )
    startup_timeout_s: float = Field(
        default=DEFAULT_STARTUP_TIMEOUT_S,
        gt=0,
        description="Seconds the Warden waits for the health probe to pass before it stops the "
        "process and reports the server dead.",
    )
    sources: tuple[_SourceKey, ...] = Field(
        min_length=1,
        max_length=MAX_SERVER_SOURCES,
        description="The [forage.map] source ids this server may load; each must name a map "
        "entry whose provider is this server's provider.",
    )
    keep_alive_s: float | None = Field(
        default=None,
        gt=0,
        description="How long a loaded model stays resident when idle, for a server that "
        "unloads on its own; None leaves the server's own default.",
    )
    load_on_start: bool = Field(
        default=True,
        description="Whether the Warden starts this server as soon as it holds its Cell; false "
        "leaves it for an explicit start.",
    )

    @field_validator("argv")
    @classmethod
    def _argv_uses_only_known_placeholders(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        """Reject a braced word in argv other than {port} and {scratch}."""
        # A typo such as {prot} would otherwise reach the server as a literal argument and fail
        # far from here, at start time on a Cell nobody is watching.
        for argument in value:
            unknown = set(_BRACED_WORD.findall(argument)) - ARGV_PLACEHOLDERS
            if unknown:
                raise ValueError(
                    f"argv names {sorted(unknown)}, but only {sorted(ARGV_PLACEHOLDERS)} are "
                    "substituted."
                )
        return value

    @field_validator("health_path")
    @classmethod
    def _health_path_is_absolute(cls, value: str) -> str:
        """Reject a health path that does not start with a slash."""
        if not value.startswith("/"):
            raise ValueError(f"health_path must start with '/', got {value!r}.")
        return value


def check_servers(servers: Mapping[str, ServerSpec], providers: Mapping[str, _ProviderRow]) -> None:
    """Raise when a server's provider is missing, not ``openai_compat`` or not on its port.

    Args:
        servers: The ``[llm.servers]`` table.
        providers: The ``[llm.providers]`` table the servers answer as.

    Raises:
        ValueError: A server names a provider with no row, a provider of another kind, a
            non-loopback ``base_url``, or a ``base_url`` whose port differs from the server's.
    """
    for server_id, spec in servers.items():
        row = providers.get(spec.provider)
        # The provider must exist: a server answering as nobody has no URL to health-probe.
        if row is None:
            raise ValueError(
                f"[llm.servers.{server_id}] names provider {spec.provider!r}, which has no "
                f"[llm.providers.{spec.provider}] entry."
            )
        # Only the OpenAI-compatible adapter can talk to a model server (ADR-0044).
        if row.kind != SERVED_KIND:
            raise ValueError(
                f"[llm.servers.{server_id}] needs an {SERVED_KIND!r} provider, but "
                f"[llm.providers.{spec.provider}] is kind {row.kind!r}."
            )
        _check_loopback_port(server_id, spec, row.base_url)


def check_server_sources(
    servers: Mapping[str, ServerSpec], forage_map: Mapping[str, _MapRow]
) -> None:
    """Raise when a server lists a source the map lacks, or one served by another provider.

    Args:
        servers: The ``[llm.servers]`` table.
        forage_map: The ``[forage.map]`` table the sources are drawn from.

    Raises:
        ValueError: A source id names no map entry, or an entry whose provider is not the
            server's own.
    """
    for server_id, spec in servers.items():
        for source_id in spec.sources:
            row = forage_map.get(source_id)
            if row is None:
                raise ValueError(
                    f"[llm.servers.{server_id}] lists source {source_id!r}, which has no "
                    f"[forage.map.{source_id}] entry."
                )
            # A source served by another provider would be health-checked on the wrong server.
            if row.provider != spec.provider:
                raise ValueError(
                    f"[llm.servers.{server_id}] lists source {source_id!r}, but "
                    f"[forage.map.{source_id}] is served by provider {row.provider!r}, not "
                    f"{spec.provider!r}."
                )


def _check_loopback_port(server_id: str, spec: ServerSpec, base_url: str) -> None:
    """Raise unless ``base_url`` is a loopback URL carrying ``spec.port``."""
    parts = urlsplit(base_url)
    hostname = parts.hostname
    # The Warden starts the process on its own Cell and probes it there: only loopback reaches it.
    if hostname is None or not is_loopback_host(hostname):
        raise ValueError(
            f"[llm.servers.{server_id}] needs a loopback base_url on "
            f"[llm.providers.{spec.provider}], got {base_url!r}."
        )
    # A port that differs would health-probe some other process while the argv starts this one.
    if parts.port != spec.port:
        raise ValueError(
            f"[llm.servers.{server_id}] listens on port {spec.port}, but "
            f"[llm.providers.{spec.provider}] base_url {base_url!r} names port {parts.port}."
        )
