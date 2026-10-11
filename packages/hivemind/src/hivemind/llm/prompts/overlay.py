"""Load a prompt overlay (a small per-model text addendum) and apply it to one model request.

A **prompt overlay** is a directory under ``overlays/`` that holds extra instructions for a class
of model that follows the shared prompts badly, such as a small local model: ``_system.md`` is
appended to every system prompt, and a file named after a ``PromptName`` (``drone_system.md``) is
appended only to that prompt. Codingrules section 8.6 keeps the shared prompts portable and says
a provider-specific tweak lives in an overlay, never in the shared file; ADR-0044 decides that an
overlay is chosen by the manifest (``[llm.providers.<name>] prompt_overlay``) and applied at the
provider door, so no call site ever knows an overlay exists.

:func:`load_overlay` reads one overlay through ``importlib.resources`` (so it works from a wheel)
into a frozen :class:`Overlay`; :func:`apply_overlay` is a pure function from an overlay and a
request to the request a provider should see. Every overlay file opens with an HTML comment that
says which model shape it is for and why; the loader strips it, so the model never reads the
maintainer's note.

Fits into the Hive:
    Layer 1 (foundational services), inside hivemind.llm. Called by the provider registry
    (``hivemind.llm.registry.overlay.OverlayingProvider``) when a provider row names an overlay;
    calls into ``hivemind.llm.prompts.loader`` for the shipped prompt bodies it keys on.

Key invariants:
    - ``apply_overlay`` is pure and never mutates its request; a request with no system prompt
      comes back unchanged, because an overlay extends a system prompt and never invents one.
    - A per-prompt suffix applies only when ``request.system`` starts with that prompt's shipped
      body (compared after ``rstrip``), so a rendered prompt with durable-state sections after it
      still matches and an unrelated system text never does; it lands directly after the body.
    - An overlay name is a plain identifier; a name that could leave the overlays directory is
      refused as not found.

See Also:
    - docs/adr/0044-local-model-serving-via-openai-compatible-api.md decision 6.
    - hivemind.llm.prompts.README for the portability rules overlay files follow too.
    - hivemind.llm.registry.overlay for the provider wrapper that applies an overlay.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from importlib.resources import files
from types import MappingProxyType
from typing import ClassVar

from hivemind.common.errors import NotFoundError
from hivemind.llm.models import LLMRequest
from hivemind.llm.prompts.loader import PromptName, load_prompt

# The directory under hivemind.llm.prompts that holds one sub-directory per overlay, resolved
# through importlib.resources like the prompt assets themselves.
_OVERLAYS_DIR = "overlays"
_SYSTEM_FILE = "_system.md"  # The file appended to every system prompt.
_NAME_PATTERN = re.compile(r"[a-z][a-z0-9_]*")  # A plain identifier: no path separators or dots.
# The maintainer's header: an HTML comment at the very top of a file, stripped before use.
_HEADER_PATTERN = re.compile(r"\A\s*<!--.*?-->\s*", re.DOTALL)

__all__ = ["Overlay", "OverlayNotFoundError", "apply_overlay", "load_overlay"]


class OverlayNotFoundError(NotFoundError):
    """Raise when a provider names a prompt overlay that no shipped directory provides."""

    code: ClassVar[str] = "hivemind.llm.overlay_not_found"

    def __init__(self, name: str) -> None:
        """Build the error for an overlay name that is not shipped.

        Args:
            name: The overlay name the manifest asked for.
        """
        super().__init__(
            f"No prompt overlay named {name!r} is shipped; "
            f"available overlays: {', '.join(available_overlays()) or 'none'}."
        )
        self.name = name


@dataclass(frozen=True, slots=True)
class Overlay:
    """One loaded overlay: text for every system prompt, and text keyed by prompt name."""

    name: str  # The directory name, as the manifest spelled it.
    system_suffix: str  # Appended to every system prompt; "" when the overlay has no _system.md.
    prompt_suffixes: Mapping[PromptName, str]  # Appended only to the keyed prompt.


def available_overlays() -> tuple[str, ...]:
    """Return the names of every shipped overlay, sorted, for an error message and tests."""
    root = files("hivemind.llm.prompts").joinpath(_OVERLAYS_DIR)
    names = (entry.name for entry in root.iterdir() if entry.is_dir())
    return tuple(sorted(name for name in names if _NAME_PATTERN.fullmatch(name)))


def load_overlay(name: str) -> Overlay:
    """Read one shipped overlay directory into a frozen value.

    Args:
        name: The overlay directory name, e.g. the manifest's ``prompt_overlay`` value.

    Returns:
        The overlay, its header comments stripped and its text trimmed.

    Raises:
        OverlayNotFoundError: `name` is not a plain identifier or no such directory ships, or the
            directory holds a markdown file that is neither ``_system.md`` nor a ``PromptName``
            (a typo that would otherwise silently do nothing).
    """
    if _NAME_PATTERN.fullmatch(name) is None:
        raise OverlayNotFoundError(name)  # Refused before touching the filesystem.
    directory = files("hivemind.llm.prompts").joinpath(_OVERLAYS_DIR, name)
    if not directory.is_dir():
        raise OverlayNotFoundError(name)

    # One pass over the directory: _system.md is the global suffix, a PromptName value a keyed one.
    known = {prompt.value: prompt for prompt in PromptName}
    system_suffix = ""
    suffixes: dict[PromptName, str] = {}
    for entry in directory.iterdir():
        if not entry.name.endswith(".md"):
            continue
        text = _strip_header(entry.read_text(encoding="utf-8"))
        stem = entry.name.removesuffix(".md")
        if entry.name == _SYSTEM_FILE:
            system_suffix = text
        elif stem in known:
            suffixes[known[stem]] = text
        else:
            raise OverlayNotFoundError(f"{name}/{entry.name}")
    return Overlay(name, system_suffix, MappingProxyType(suffixes))


def apply_overlay(overlay: Overlay, request: LLMRequest) -> LLMRequest:
    """Return `request` with the overlay's text added to its system prompt.

    Args:
        overlay: The overlay a provider row selected.
        request: The call about to reach the provider.

    Returns:
        `request` itself when it has no system prompt or the overlay adds nothing to it,
        otherwise a copy whose ``system`` has the matching per-prompt suffix directly after the
        shipped body (before any labelled state sections, so the static prefix stays contiguous
        for provider caching) and the ``_system.md`` suffix at the very end, each after a blank
        line.
    """
    if request.system is None:
        return request
    system = request.system.rstrip()
    keyed = _insert_keyed_suffix(overlay, system)
    if overlay.system_suffix:
        keyed = f"{keyed}\n\n{overlay.system_suffix}"
    if keyed == system:
        return request
    return request.model_copy(update={"system": keyed})


def _insert_keyed_suffix(overlay: Overlay, system: str) -> str:
    """Return `system` with the matching prompt's suffix placed right after its shipped body."""
    for prompt, suffix in overlay.prompt_suffixes.items():
        body = load_prompt(prompt).rstrip()
        # The rendered prompt is the shipped body plus labelled sections, so a prefix match is the
        # only test that survives sections being appended after it.
        if system.startswith(body):
            return f"{body}\n\n{suffix}{system[len(body) :]}"
    return system


def _strip_header(text: str) -> str:
    """Drop the leading HTML-comment header, which is for maintainers and not for the model."""
    return _HEADER_PATTERN.sub("", text, count=1).strip()
