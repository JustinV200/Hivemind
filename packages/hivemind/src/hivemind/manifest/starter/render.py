"""Render a starter Hive Manifest for one provider path: the file `hive init` writes.

A newcomer's first Hive needs a manifest that validates, binds every model slot, gives the Drone a
Forage footprint and enables the Hive Stand (this machine, the first Real Cell), so `hive run`
works before Docker is installed (roadmap 14.1 and 14.2). Writing that by hand is the first wall a
newcomer hits, so this module fills a shipped template instead: `anthropic.toml` for the hosted
path, `local.toml` for one OpenAI-compatible server (Ollama, LM Studio, vLLM, llama.cpp). Model ids
and base URLs live only in those templates and in this module, which sits under `manifest/`
where codingrules 8.6 allows them. Every rendered text is validated as a `HiveManifest` before it
is returned, so `hive init` never writes a file `hive run` would refuse.

Fits into the Hive:
    Layer 1 (foundational services; configuration). Called by `hivemind.cli.init`. Calls into
    `hivemind.manifest.schema` and `waggle` (ids, the clock, the loopback rule) only.

Key invariants:
    - Pure apart from reading the shipped template: no file is written here.
    - Every value spliced into a template is TOML-quoted, so a name or model id can never break
      out of its string.
    - The returned text always validates as a `HiveManifest` with `[hive_stand] enabled = true`.

See Also:
    - docs/manifests/minimal.toml and docs/manifests/local.toml, the hand-written shapes these
      templates follow.
    - hivemind.cli.init for the command that writes the result.
"""

from __future__ import annotations

import json
import tomllib
from dataclasses import dataclass
from enum import StrEnum
from importlib.resources import files
from string import Template
from urllib.parse import urlsplit

from pydantic import ValidationError

from hivemind.manifest.errors import ManifestError
from hivemind.manifest.schema import HiveManifest
from waggle.clock import Clock
from waggle.ids import new_hive_id, new_node_id
from waggle.uris import is_loopback_host

# Ollama's own OpenAI-compatible root on its default port: the one base URL a newcomer on the
# Ollama path never has to look up. Allowed here because this module is part of the manifest.
OLLAMA_BASE_URL = "http://127.0.0.1:11434/v1"
_HOSTED_TEMPLATE = "anthropic.toml"  # The hosted path's template, beside this module.
_LOCAL_TEMPLATE = "local.toml"  # Every OpenAI-compatible server's template, beside this module.

__all__ = ["OLLAMA_BASE_URL", "StarterChoice", "StarterProvider", "render_starter"]


class StarterProvider(StrEnum):
    """The provider paths `hive init --provider` offers (roadmap 14.1's "choose a provider")."""

    ANTHROPIC = "anthropic"  # Hosted; the template binds its own models.
    OPENAI_COMPATIBLE = "openai-compatible"  # Any server speaking the OpenAI API; base URL given.
    OLLAMA = "ollama"  # An OpenAI-compatible server at Ollama's default address.


@dataclass(frozen=True, slots=True)
class StarterChoice:
    """What the operator chose: the provider path and the few values a template cannot guess.

    Attributes:
        provider: The provider path.
        name: The Hive's human-facing label; empty for none.
        model: The model every chat slot binds to; required on a local path, refused on the
            hosted one (its template names the models).
        embed_model: The embedder slot's model; the chat model when None.
        base_url: The server's `/v1` root; required for `openai-compatible`, Ollama's default
            when None on `ollama`, refused on the hosted path.
    """

    provider: StarterProvider
    name: str = ""
    model: str | None = None
    embed_model: str | None = None
    base_url: str | None = None


def render_starter(choice: StarterChoice, clock: Clock) -> str:
    """Render and validate the starter manifest's text for `choice`, with fresh Hive ids.

    Args:
        choice: The provider path and its values.
        clock: Mints the Hive id and node id (ids carry their creation time).

    Returns:
        TOML text that validates as a `HiveManifest`.

    Raises:
        ManifestError: A value the path needs is missing, a value it ignores was given, or the
            rendered text fails validation (an unusable model id, say).
    """
    values = {
        "hive_id": _quoted(new_hive_id(clock)),
        "node_id": _quoted(new_node_id(clock)),
        "name": _quoted(choice.name),
    }
    # The hosted path's template names its models; a local one is filled from the choice.
    if choice.provider is StarterProvider.ANTHROPIC:
        _refuse_local_values(choice)
        template = _HOSTED_TEMPLATE
    else:
        values |= _local_values(choice)
        template = _LOCAL_TEMPLATE
    text = Template(_read_template(template)).substitute(values)
    _validate(text)
    return text


def _refuse_local_values(choice: StarterChoice) -> None:
    """Refuse a model or base URL on the hosted path, rather than silently dropping it."""
    if choice.model or choice.embed_model or choice.base_url:
        raise ManifestError(
            "--model, --embed-model and --base-url apply to a model server; the anthropic "
            "starter binds its own models (edit the file afterwards to change them)."
        )


def _local_values(choice: StarterChoice) -> dict[str, str]:
    """Return the local template's values: server, models, and offline when it is loopback."""
    if not choice.model:
        raise ManifestError(
            f"--provider {choice.provider} needs --model: the id the server serves."
        )
    base_url = choice.base_url
    # Ollama answers at a known address; any other server's address cannot be guessed.
    if base_url is None and choice.provider is StarterProvider.OLLAMA:
        base_url = OLLAMA_BASE_URL
    if not base_url:
        raise ManifestError(
            "--provider openai-compatible needs --base-url (the server's /v1 root)."
        )
    # Offline is the honest default for a server on this machine: nothing then leaves it.
    offline = is_loopback_host(urlsplit(base_url).hostname or "")
    return {
        "provider": choice.provider.value,
        "base_url": _quoted(base_url),
        "model": _quoted(choice.model),
        "embed_model": _quoted(choice.embed_model or choice.model),
        "offline": "true" if offline else "false",
    }


def _quoted(value: str) -> str:
    """Return `value` as a TOML basic string; JSON's escapes are all valid TOML escapes."""
    return json.dumps(value, ensure_ascii=False)


def _read_template(name: str) -> str:
    """Read one shipped template from this package's resources."""
    return (files(__package__) / name).read_text(encoding="utf-8")


def _validate(text: str) -> None:
    """Raise ManifestError unless `text` parses and validates as a `HiveManifest`."""
    try:
        HiveManifest.model_validate(tomllib.loads(text))
    except (tomllib.TOMLDecodeError, ValidationError) as exc:
        raise ManifestError(f"The starter manifest does not validate: {exc}") from exc
