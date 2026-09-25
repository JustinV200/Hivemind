"""Define one provider row, the provider kinds, and the offline and secret rules every door shares.

`ProviderConfig` is this package's own, decoupled mirror of one `[llm.providers.<name>]` row
(`hivemind.manifest.schema.llm.ProviderSpec`): `hivemind.llm` cannot import `hivemind.manifest`
(codingrules section 4 places them as independent Layer 1 siblings), so the composition root (the
CLI, roadmap step 3.21) builds one of these per provider from a loaded `HiveManifest`. The kind
sets say which door a kind can open: every kind has a chat factory, a transcription factory, an
embedding factory, or more than one. `_check_offline` enforces `[llm] offline = true` a second
time, at construction, on top of the manifest's own load-time check -- belt and braces, and the
only way to catch a provider kind (`ANTHROPIC`) whose *default* endpoint is hosted even when its
`base_url` is left empty.

Fits into the Hive:
    Layer 1 (foundational services). Read by every door of the registry (`chat`, `transcription`,
    `embedding`, `provider_registry`); calls into `hivemind.common.errors`, `hivemind.llm.errors`
    and `waggle.uris` only.

Key invariants:
    - `ProviderKind`, `EMBEDDING_ONLY_KINDS` and `IN_PROCESS_KINDS` mirror
      `hivemind.manifest.schema.llm`'s own copies member-for-member; a dedicated test keeps the two
      sides in sync.
    - `PENDING_KINDS` names every `ProviderKind` no factory table covers. It is empty now that
      every adapter is wired, and stays so the completeness test and the next adapter have a home.
    - An `IN_PROCESS_KINDS` member is provably local regardless of its `base_url`, because it never
      opens one at all; every other kind must name a loopback or Virtual Cell gateway host offline.
    - `_resolve_api_key` is the only place the registry reads an environment mapping; it mirrors
      `hivemind.manifest.env.provider_api_key`'s derivation so a provider's secret still comes from
      exactly one environment variable, never the config itself (codingrules section 13).

See Also:
    - .claude/codingrules.md section 8.6 for "offline is a first-class mode" and "one door".
    - hivemind.llm.registry.provider_registry for the registry these rows configure.
    - docs/adr/0033-transcription-provider-whisper-first.md and
      docs/adr/0036-embedding-provider-and-reembedding-policy.md for the two non-chat kinds.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import ClassVar, Literal
from urllib.parse import urlsplit

from pydantic import SecretStr

from hivemind.common.errors import InvariantViolationError
from hivemind.llm.errors import LLMError, OfflineViolationError
from waggle.clock import Clock
from waggle.uris import is_loopback_host, is_virtual_cell_gateway_host

# Mirrors hivemind.manifest.schema.llm.ProviderKind member-for-member; llm may not import manifest
# (codingrules section 4), so this is this module's own copy, kept in sync by a dedicated test.
# "whisper_local" is transcription-only (roadmap step 6.5a); "sentence_transformers" is
# embedding-only (roadmap step 7.1).
ProviderKind = Literal[
    "anthropic", "openai_compat", "fake", "whisper_local", "sentence_transformers"
]

# Kinds with no adapter wired yet, in no factory table; the completeness test excludes exactly this
# set from "every ProviderKind has at least one factory".
PENDING_KINDS: frozenset[ProviderKind] = frozenset()

# Kinds with no chat factory that serve only the EMBEDDER slot (roadmap 7.1); the manifest (its own
# mirror of this set) refuses to bind any other slot to one.
EMBEDDING_ONLY_KINDS: frozenset[ProviderKind] = frozenset({"sentence_transformers"})

# Mirrors hivemind.manifest.schema.llm.IN_PROCESS_KINDS (same sync test): kinds whose model runs
# inside this process, local by construction, so offline mode checks no base_url for them.
IN_PROCESS_KINDS: frozenset[ProviderKind] = frozenset({"whisper_local", "sentence_transformers"})

__all__ = [
    "EMBEDDING_ONLY_KINDS",
    "IN_PROCESS_KINDS",
    "PENDING_KINDS",
    "MissingDefaultModelError",
    "ProviderConfig",
    "ProviderKind",
    "TranscriptionUnsupportedError",
]


class MissingDefaultModelError(InvariantViolationError):
    """Raise when an openai_compat ProviderConfig reaches its factory with no default_model.

    The composition root derives `default_model` from the provider's first `[llm.slots]` row
    before building a registry, so reaching this means Hive code skipped that step: a bug in
    the Hive, not bad operator input (codingrules section 10), and named so the trail can say
    which invariant broke.
    """

    code: ClassVar[str] = "hivemind.llm.missing_default_model"

    def __init__(self, name: str) -> None:
        """Build the error for provider `name`.

        Args:
            name: The `[llm.providers.<name>]` key whose config lacks a default model.
        """
        super().__init__(
            f"[llm.providers.{name}] is kind='openai_compat' but no default_model was derived for "
            "it; the composition root must supply one (e.g. from its first [llm.slots] row)."
        )
        self.name = name


class TranscriptionUnsupportedError(LLMError):
    """Raise when a transcriber binding names a provider whose kind cannot transcribe.

    A manifest may legally bind `[llm.slots.transcriber]` to any declared provider (the slot
    table is checked for shape, not for fit), so this is operator input the registry refuses at
    first use, naming the kind so the fix is obvious: bind the slot to a `whisper_local` or
    `openai_compat` provider instead.
    """

    code: ClassVar[str] = "hivemind.llm.transcription_unsupported"

    def __init__(self, provider: str, kind: str) -> None:
        """Build the error for provider `provider` of kind `kind`.

        Args:
            provider: The `[llm.providers.<name>]` key the binding named.
            kind: That provider's kind, which has no transcription factory.
        """
        super().__init__(
            f"[llm.providers.{provider}] is kind={kind!r}, which cannot transcribe; bind "
            "ModelSlot.TRANSCRIBER to a 'whisper_local' or 'openai_compat' provider.",
            provider=provider,
        )
        self.kind = kind


@dataclass(frozen=True, slots=True)
class ProviderConfig:
    """One `[llm.providers.<name>]` row, decoupled from `hivemind.manifest.schema.llm.ProviderSpec`.

    Mirrors that model's fields (see the module docstring for why this module cannot import it
    directly); the composition root builds one of these per provider from a loaded HiveManifest.
    """

    kind: ProviderKind  # Which factory table entry builds this provider, per door.
    base_url: str  # "" means a hosted API with a vendor-fixed endpoint; non-empty for a local one.
    api_key_env: str | None = None  # HIVEMIND_<NAME>_API_KEY is derived when this is None.
    timeout_s: float = 120.0  # Mirrors manifest.schema.llm.DEFAULT_PROVIDER_TIMEOUT_S's default.
    capability_overrides: Mapping[str, bool | int] = field(default_factory=dict)  # From
    # ProviderSpec.capabilities.as_overrides(): only the fields a manifest author actually set.
    default_model: str | None = None  # Required for kind="openai_compat"; unused by every other.
    # The three fields below mirror hivemind.manifest.schema.llm.EmbeddingOptions (roadmap 7.1);
    # unused by a provider this Hive never asks to embed. embedding_local_files_only is the one
    # the composition root (cli/stores.provider_configs) forces True whenever [llm] offline = true,
    # regardless of what the manifest itself set (an in-process embedder must never reach a model
    # hub on a Hive proven offline).
    embedding_batch_size: int | None = None  # None lets an embedding factory's own default decide.
    embedding_device: str | None = None  # None lets the in-process library choose a device.
    embedding_local_files_only: bool = False  # Never reach a model hub to load an embedder.


@dataclass(frozen=True, slots=True)
class _DoorContext:
    """What every door of the registry builds from: the rows, the offline flag, secrets, a clock.

    Shared by the transcription and embedding doors (`TranscriberDoor`, `EmbedderDoor`) so each
    takes one argument group instead of four (codingrules 5.1), and so all three doors read the
    same rows and the same offline flag the `ProviderRegistry` that owns them was built with.
    """

    providers: Mapping[str, ProviderConfig]  # Every [llm.providers.<name>] row, by name.
    offline: bool  # [llm] offline: checked before any factory runs.
    environ: Mapping[str, str]  # Read only through _resolve_api_key (codingrules section 13).
    clock: Clock  # Passed to every factory and, through it, to every constructed provider.


def _check_offline(name: str, config: ProviderConfig, offline: bool) -> None:
    """Raise OfflineViolationError when `offline` and `config` is not provably local.

    Mirrors hivemind.manifest.schema.llm's own (private) loopback rule from the same public
    primitive, `waggle.uris.is_loopback_host`, rather than importing a name manifest does not
    export. An `IN_PROCESS_KINDS` member never opens a `base_url` at all (roadmap steps 6.5a and
    7.1), so it is provably local by construction, whatever `base_url` happens to hold.
    """
    if not offline:
        return
    if config.kind in IN_PROCESS_KINDS:
        return
    if not _is_provably_local(config.base_url):
        raise OfflineViolationError(name, config.base_url)


def _is_provably_local(base_url: str) -> bool:
    """Return True when `base_url` names a loopback host or a Virtual Cell gateway host.

    This registry is shared by the Hive Stand's own composition root and by a Virtual Cell's own
    in-Cell registry (`hivemind.cli.in_cell.providers.build_in_cell_provider_registry`): a
    provider's `base_url` a Cell resolves has already been rewritten to the gateway alias it
    reaches the Hive Stand through (`host.docker.internal`, QEMU's `10.0.2.2`), which
    `is_loopback_host` alone would reject as "not local" -- from inside the Cell, that gateway
    address IS the Hive Stand's own machine (the same reasoning `waggle.uris.check_waggle_uri`'s
    `allow_virtual_cell_gateway_host` already applies to the Waggle control link).
    `is_virtual_cell_gateway_host` is the minimal, documented carve-out for that one extra case;
    every other host still fails exactly as before.
    """
    if not base_url:
        return False  # Empty means a hosted, vendor-fixed endpoint (e.g. Anthropic): never local.
    hostname = urlsplit(base_url).hostname
    if hostname is None:
        return False
    return is_loopback_host(hostname) or is_virtual_cell_gateway_host(hostname)


def _resolve_api_key(
    name: str, api_key_env: str | None, environ: Mapping[str, str]
) -> SecretStr | None:
    """Read one provider's API key from `environ`, mirroring hivemind.manifest.env.provider_api_key.

    That function cannot be called directly here: it takes a ProviderSpec, and llm may not
    import manifest (see the module docstring). This is the same short derivation, kept here so
    `RegistryDeps.environ` stays the only environment read the registry performs (codingrules
    section 13: "environment variables are read in exactly one place").
    """
    var_name = api_key_env or f"HIVEMIND_{name.upper()}_API_KEY"
    raw = environ.get(var_name)
    return SecretStr(raw) if raw is not None else None
