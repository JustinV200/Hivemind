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

Fits into the Hive:
    Layer 1 (foundational services; capacity as data). Called by `hivemind.cli.in_cell.borrowed`,
    the composition root of a colonized device's Warden. Calls into `hivemind.manifest.errors` and
    `hivemind.manifest.schema` only.

Key invariants:
    - The only section is `[llm]`; an unknown top-level key fails validation (`extra="forbid"`).
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

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from hivemind.manifest.errors import ManifestError
from hivemind.manifest.schema import LlmSection

__all__ = ["DeviceManifest", "load_device_manifest"]


class DeviceManifest(BaseModel):
    """What a colonized device's Warden is configured from: the `[llm]` section, nothing else.

    Read once at the Warden's start from a file on the device. It crosses no boundary after that:
    `hivemind.cli.in_cell.borrowed` converts it into the provider, slot and server tables the
    Warden is built with.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    llm: LlmSection = Field(
        description="The device's model providers, the slot each bee uses, and (for a device that "
        "could host one) the model servers it may start; the Hive Manifest's own `[llm]` shape."
    )


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
