"""Translate images and audio into OpenAI-compatible chat-completions content parts.

A model sees pixels or hears sound on this wire only as parts of a message's `content` array: an
image as an `image_url` part carrying a data URL, an audio clip as an `input_audio` part carrying
base64 and a container name. Either may arrive two ways: as a part of a turn itself, or (roadmap
step 6.5) as media a tool result carries beside its text, a screenshot from the Exoskeleton's
`see` or a recording from its `listen`. A `role: "tool"` message is a plain string on this wire, so
tool-result media cannot ride inside it; `media_turn` builds the one user message that follows a
turn's tool messages and carries every piece of their media, each run labelled with the call that
returned it. Split out of its sibling `mapping.py` by codingrules 5.1's size limit, like
`rate_limit.py`; like both, it is one of this package's private files where an OpenAI wire field
name (`image_url`, `input_audio`) may appear (codingrules section 8.6).

Fits into the Hive:
    Layer 1 (foundational services; capacity as data), inside `hivemind.llm.providers.
    openai_compat`. Called by `mapping.request_to_json` for every image or audio part and every
    tool result with media. Calls into `hivemind.llm.models`, `hivemind.llm.capabilities` and
    `hivemind.llm.errors` only.

Key invariants:
    - Content is refused, never dropped: an image without `capabilities.vision`, audio without
      `capabilities.audio`, or audio in a container `input_audio` cannot name raises
      `ProviderRequestError` before any HTTP call is made.
    - `media_turn` returns None when no tool result carries media, so a text-only tool round
      maps to exactly the messages it mapped to before media existed.

See Also:
    - hivemind.llm.providers.openai_compat.mapping for request_to_json, this module's one caller.
    - hivemind.llm.models for ImagePart, AudioPart and ToolResultPart.media.
    - .claude/codingrules.md section 8.6 for "capabilities are declared, not assumed".
"""

from __future__ import annotations

from collections.abc import Sequence

from pydantic import JsonValue

from hivemind.llm.capabilities import ProviderCapabilities
from hivemind.llm.errors import ProviderRequestError
from hivemind.llm.models import AudioPart, ImagePart, ToolResultPart

# A synthesized status for content refused before any HTTP call: no real response backs it.
UNSUPPORTED_CONTENT_STATUS_CODE = 400
# The containers `input_audio` names, keyed by the MIME type an AudioPart states. Anything else is
# refused rather than sent under a format name the server would misread.
_AUDIO_FORMATS: dict[str, str] = {
    "audio/wav": "wav",
    "audio/wave": "wav",
    "audio/x-wav": "wav",
    "audio/mpeg": "mp3",
    "audio/mp3": "mp3",
}

__all__ = ["UNSUPPORTED_CONTENT_STATUS_CODE", "media_to_wire", "media_turn"]


def media_to_wire(
    part: ImagePart | AudioPart, capabilities: ProviderCapabilities, provider: str
) -> JsonValue:
    """Map one image or audio part to its content part: `image_url` or `input_audio`.

    Args:
        part: The image or the clip.
        capabilities: This binding's declared capabilities; `vision` gates an image, `audio` a
            clip.
        provider: The manifest provider name, for the refusal.

    Returns:
        The content part, ready for a message's `content` array.

    Raises:
        ProviderRequestError: The binding does not declare the capability the part needs, or the
            clip's container is not one `input_audio` can name.
    """
    if isinstance(part, ImagePart):
        return _image_to_wire(part, capabilities, provider)
    return _audio_to_wire(part, capabilities, provider)


def media_turn(
    results: Sequence[ToolResultPart], capabilities: ProviderCapabilities, provider: str
) -> JsonValue | None:
    """Build the user message that carries the media of `results`, or None when none has any.

    Args:
        results: One turn's tool results, in order; most carry no media at all.
        capabilities: This binding's declared capabilities, checked for every piece.
        provider: The manifest provider name, for a refusal.

    Returns:
        One `role: "user"` message whose content labels each result's media with the call id
        that returned it, then carries that media, in order; None when no result has media.

    Raises:
        ProviderRequestError: A piece of media this binding cannot take.
    """
    content: list[JsonValue] = []
    # One labelled run per result with media, so a model given two screenshots in one turn can
    # still tell which call returned which.
    for result in results:
        if not result.media:
            continue
        content.append({"type": "text", "text": f"Returned by tool call {result.call_id}:"})
        content.extend(media_to_wire(part, capabilities, provider) for part in result.media)
    if not content:
        return None
    return {"role": "user", "content": content}


def _image_to_wire(part: ImagePart, capabilities: ProviderCapabilities, provider: str) -> JsonValue:
    """Map an `ImagePart` to an `image_url` data-URL content part, or refuse it without vision."""
    if not capabilities.vision:
        raise _unsupported(
            provider,
            "this provider's capabilities declare vision=False; an ImagePart was in the request "
            "and cannot be sent",
        )
    data_url = f"data:{part.media_type};base64,{part.data_base64}"
    return {"type": "image_url", "image_url": {"url": data_url}}


def _audio_to_wire(part: AudioPart, capabilities: ProviderCapabilities, provider: str) -> JsonValue:
    """Map an `AudioPart` to an `input_audio` content part, or refuse it without audio."""
    if not capabilities.audio:
        raise _unsupported(
            provider,
            "this provider's capabilities declare audio=False; an AudioPart was in the request "
            "and cannot be sent (transcribe it on the transcriber slot instead)",
        )
    audio_format = _AUDIO_FORMATS.get(part.media_type.lower())
    if audio_format is None:
        raise _unsupported(
            provider, f"input_audio carries wav or mp3, not {part.media_type!r} audio"
        )
    clip: JsonValue = {"data": part.data_base64, "format": audio_format}
    return {"type": "input_audio", "input_audio": clip}


def _unsupported(provider: str, detail: str) -> ProviderRequestError:
    """Build the refusal for content this binding cannot take, before any HTTP call is made."""
    return ProviderRequestError(
        provider,
        status_code=UNSUPPORTED_CONTENT_STATUS_CODE,
        error_type="unsupported_content",
        detail=detail,
    )
