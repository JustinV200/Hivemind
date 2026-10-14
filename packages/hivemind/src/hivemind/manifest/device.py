"""Define DeviceManifest: the small TOML file a colonized Swarm device's Warden reads.

A Warden that runs on a device the Hive borrows (Level 1 of the runtime ladder, roadmap step 11.6a)
has no Hive Manifest: the Hive's files live on the Hive Stand, and the device is not meant to hold
a copy of them. What it does need is the part of the Hive's configuration that depends on where the
device is, namely which model providers it can reach from there (`[llm.providers]`), which slot
each of its bees uses (`[llm.slots]`), and, for a device that could become a Nuc (Level 2), the
model servers its operator declares may be started on it (`[llm.servers]`). The operator writes
exactly that, in the Hive Manifest's own `[llm]` shape, into a file the Pollen Packet hands the
Warden (`pollen install --warden-manifest`). `DeviceManifest` is that file's model and
`load_device_manifest` its loader: the section is the same `LlmSection` the Hive Manifest embeds, so
every rule that keeps a slot table honest holds here unchanged, and any other section is refused
rather than ignored, because a key the Warden would silently not read is a mistake worth surfacing.
The one thing besides `[llm]` is `[forage.map]`, and only the rows the device's own servers list:
a server names the Forage map entries (models) it may load by id, and the Warden that starts it
must know what each one is (which provider and model it serves, its context window, the VRAM it
needs), which the Hive's manifest knows and the device has no copy of, so the operator copies
those rows across, exactly as they copy the `[llm]` section.

Fits into the Hive:
    Layer 1 (foundational services; capacity as data). Called by `hivemind.cli.in_cell.borrowed`,
    the composition root of a colonized device's Warden. Calls into `hivemind.manifest.errors` and
    `hivemind.manifest.schema` only.

Key invariants:
    - The sections are `[llm]` and `[forage.map]`; an unknown top-level key fails validation
      (`extra="forbid"`).
    - Every source an `[llm.servers]` row lists has its `[forage.map]` row, served by that server's
      provider, and every row is listed by some server: a model the Warden could not describe, or
      a row nothing reads, is refused at load and not at the first promotion.
    - `[llm]` is required and complete: a device with no providers would have a Warden whose every
      model call fails or, worse, answers from a stand-in, so an empty file is no device manifest.
    - API keys are never in the file (the schema has no such field); each provider's key comes from
      the device's own environment, as everywhere else (codingrules section 13).
    - `load_device_manifest` never returns a partial manifest and never lets the raw TOML `dict`
      out (codingrules section 9).

See Also:
    - docs/adr/0052-runtime-ladder-warden-moves-first.md for "the device carries its own
      configuration".
    - docs/manifests/device.toml for an annotated example.
    - hivemind.manifest.loader for `load_manifest`, the Hive Manifest's own loader.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from hivemind.forage import ModelSourceSpec
from hivemind.manifest.errors import ManifestError
from hivemind.manifest.schema import LlmSection
from hivemind.manifest.schema.llm.providers import ManifestKey
from hivemind.manifest.schema.llm.servers import check_server_sources

__all__ = ["DeviceForageSection", "DeviceManifest", "load_device_manifest"]


class DeviceForageSection(BaseModel):
    """`[forage]` in a device manifest: the map rows of the models its servers may load."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    map: dict[ManifestKey, ModelSourceSpec] = Field(
        default_factory=dict,
        description="[forage.map.<source_id>]: the Hive Manifest's rows for the models this "
        "device's [llm.servers] list, copied across. A row that names a host is refused, since a "
        "Warden fills in its own Cell when it loads a model.",
    )


class DeviceManifest(BaseModel):
    """What a colonized device's Warden is configured from: `[llm]` and its servers' map rows.

    Read once at the Warden's start from a file on the device. It crosses no boundary after that:
    `hivemind.cli.in_cell.borrowed` converts it into the provider, slot and server tables the
    Warden is built with.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    llm: LlmSection = Field(
        description="The device's model providers, the slot each bee uses, and (for a device that "
        "could host one) the model servers it may start; the Hive Manifest's own `[llm]` shape."
    )
    forage: DeviceForageSection = Field(
        default_factory=DeviceForageSection,
        description="The [forage.map] rows of the models those servers may load; empty for a "
        "device that declares no server.",
    )

    @model_validator(mode="after")
    def _servers_and_map_rows_match(self) -> DeviceManifest:
        """Require a map row for every listed source, and a server listing every row."""
        # The Hive Manifest's own cross-table rule (a listed source has a row served by the
        # server's provider), run here because this file is the one place both tables are in view.
        check_server_sources(self.llm.servers, self.forage.map)
        listed = {source for server in self.llm.servers.values() for source in server.sources}
        for source_id, row in self.forage.map.items():
            if source_id not in listed:
                raise ValueError(
                    f"[forage.map.{source_id}] is listed by no [llm.servers] row, so nothing on "
                    "this device would read it."
                )
            if row.host_cell_id is not None:
                raise ValueError(
                    f"[forage.map.{source_id}] names a host Cell; the Warden that loads the model "
                    "fills in its own, so the row must leave host_cell_id out."
                )
        return self


def load_device_manifest(path: Path) -> DeviceManifest:
    """Load and validate the device manifest at `path`.

    Args:
        path: The TOML file `pollen install --warden-manifest` recorded.

    Returns:
        The validated manifest.

    Raises:
        ManifestError: `path` cannot be read, is not valid TOML, or fails validation (a missing or
            incomplete `[llm]`, or a section other than `[llm]`).
    """
    try:
        with path.open("rb") as handle:
            # tomllib wants a binary handle and decodes the document as UTF-8 itself.
            raw = tomllib.load(handle)
    except OSError as exc:
        raise ManifestError(f"Could not read device manifest {path}: {exc}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise ManifestError(f"Device manifest {path} is not valid TOML: {exc}") from exc

    try:
        return DeviceManifest.model_validate(raw)
    except ValidationError as exc:
        # One sentence per problem, each with its dotted location, so an operator on a device
        # with no other tooling can find the line to fix (the Hive Manifest's loader does the same).
        parts = [
            f"{'.'.join(str(part) for part in error['loc']) or '<root>'}: {error['msg']}"
            for error in exc.errors()
        ]
        raise ManifestError(
            f"Device manifest {path} failed validation: {'; '.join(parts)}"
        ) from exc
