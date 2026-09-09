"""Define ManifestError: the one error a Hive Manifest failing to load or validate raises.

A Hive Manifest (the TOML file that configures one running Hive) can fail in exactly two ways
before it becomes a usable ``HiveManifest``: the file cannot be found or is not valid TOML, or its
content fails one of ``HiveManifest``'s own pydantic validators (a missing required field, a
provider a slot binding names but never declares, an offline provider that is not loopback, and so
on). Both failures are reported through this one class so a caller (the CLI, a test, the
composition root) can catch a single type regardless of which stage failed. Codingrules section 13
requires this be a ``ConfigurationError`` subclass: "Raise when a Hive Manifest is missing,
malformed, or invalid."

Fits into the Hive:
    Layer 1 (foundational services; capacity as data). Raised by ``hivemind.manifest.loader`` and
    ``hivemind.manifest.env``, the only two modules that ever construct one. Calls into
    ``hivemind.common.errors`` only.

Key invariants:
    - Every ManifestError names the manifest file's path (or the offending environment variable)
      and, for a validation failure, the dotted field location pydantic reported, so a message is
      never just "invalid manifest" with nothing to act on.

See Also:
    - .claude/codingrules.md section 10 for the "one root per subsystem" error rule this follows.
    - .claude/codingrules.md section 13 for "raise when a Hive Manifest is missing, malformed, or
      invalid".
    - hivemind.common.errors for ConfigurationError, the root this subclasses.
    - hivemind.manifest.loader for load_manifest, the main raiser.
"""

from __future__ import annotations

from typing import ClassVar

from hivemind.common.errors import ConfigurationError

__all__ = ["ManifestError"]


class ManifestError(ConfigurationError):
    """Raise for any Hive Manifest failure: unreadable file, bad TOML, or failed validation."""

    code: ClassVar[str] = "hivemind.manifest_error"
