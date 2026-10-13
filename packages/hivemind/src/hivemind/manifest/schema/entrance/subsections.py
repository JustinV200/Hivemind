"""Define the ``[entrance.tls]``, ``[entrance.push]`` and ``[entrance.voice]`` sub-tables.

Three of the Hive Entrance's sub-tables are small and independent of the listeners' own settings,
so they live apart from ``EntranceSection`` (codingrules 5.2): the remote listener's certificate
and key by path (``tls``), which push channels the Entrance offers devices (``push``), and audio in
at the chat route (``voice``). Each model is frozen, forbids unknown fields and carries the
defaults codingrules section 13 documents. The one cross-field rule lives here: a device's audio
budget may never be smaller than the longest clip, or that clip could never be heard.

Fits into the Hive:
    Layer 1 (foundational services; capacity as data), inside ``hivemind.manifest.schema.entrance``.
    Embedded by ``EntranceSection``; read by ``hivemind.entrance`` (Layer 7). Calls into pydantic
    only.

Key invariants:
    - Every model here is frozen and forbids unknown fields (codingrules section 8.5).
    - No secret lives here: the VAPID key and TLS private key material come from the secret
      store or ``HIVEMIND_*`` variables, never this section (codingrules section 13).

See Also:
    - docs/adr/0041-landing-board-enrolment-two-factor-login-and-exposure.md for the TLS rules.
    - docs/adr/0042-landing-board-versioning-and-push.md for the push channels.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator

_MAX_WEBHOOK_ALLOWLIST = 64  # Extra webhook destinations; a real Hive lists a handful.
_MODEL_CONFIG = ConfigDict(frozen=True, extra="forbid")  # Frozen, no strays (codingrules 8.5).

__all__ = ["EntrancePushSection", "EntranceTlsSection", "EntranceVoiceSection"]


class EntranceTlsSection(BaseModel):
    """``[entrance.tls]``: the remote listener's own certificate and key, by path."""

    model_config = _MODEL_CONFIG

    cert: str = Field(
        default="",
        description="Path to the remote listener's PEM certificate chain; empty means no TLS. "
        "Required for lan and tunnel, and for browsers in any mode (a passkey needs a secure "
        "origin), e.g. the output of `tailscale cert` for the overlay's DNS name.",
    )
    key: str = Field(
        default="",
        description="Path to the PEM private key for `cert`; a file path, never the key itself.",
    )


class EntrancePushSection(BaseModel):
    """``[entrance.push]``: which push channels the Entrance offers devices (ADR-0042)."""

    model_config = _MODEL_CONFIG

    webhooks: bool = Field(default=True, description="Offer signed webhooks to program devices.")
    web_push: bool = Field(
        default=True,
        description="Offer Web Push to browsers and phones; the VAPID key comes from "
        "HIVEMIND_ENTRANCE_VAPID_PRIVATE_KEY (or the secret store) and its contact from "
        "HIVEMIND_ENTRANCE_VAPID_SUBJECT.",
    )
    webhook_allowlist: tuple[str, ...] = Field(
        default=(),
        max_length=_MAX_WEBHOOK_ALLOWLIST,
        description="Hosts or CIDR networks a webhook may target beyond the default rule (an "
        "https URL, or an address inside vpn_cidrs); never loopback, link-local or the Hive "
        "Stand's own addresses, whatever this lists (ADR-0042).",
    )


class EntranceVoiceSection(BaseModel):
    """``[entrance.voice]``: audio in at the Landing Board, transcribed on the TRANSCRIBER slot."""

    model_config = _MODEL_CONFIG

    enabled: bool = Field(
        default=True, description="Accept audio on the chat route and the chat WebSocket."
    )
    confirm_goals: bool = Field(
        default=True,
        description="Echo a spoken goal back to the device for confirmation before it becomes "
        "a task, so a misheard sentence never spends anything; answers and chat go straight "
        "through.",
    )
    keep_audio: bool = Field(
        default=False,
        description="Keep a clip after transcription as C2 Nectar with a retention window; "
        "false discards it once transcribed.",
    )
    max_clip_seconds: float = Field(
        default=120.0, gt=0, description="The longest clip accepted, in seconds."
    )
    audio_seconds_per_minute: float = Field(
        default=120.0,
        gt=0,
        description="Seconds of audio one device may send a minute: a token bucket of that many "
        "seconds, refilling at that rate, charged each clip's length before it is transcribed. "
        "At least max_clip_seconds, so the longest clip always fits a full bucket.",
    )
    keep_audio_hours: float = Field(
        default=24.0,
        gt=0,
        description="How long a kept clip stays as C2 Nectar before the retention sweep deletes "
        "it; read only when keep_audio is true.",
    )

    @model_validator(mode="after")
    def _budget_fits_a_clip(self) -> EntranceVoiceSection:
        """Refuse an audio budget smaller than the longest clip: that clip could never be heard."""
        if self.audio_seconds_per_minute < self.max_clip_seconds:
            raise ValueError(
                f"[entrance.voice] audio_seconds_per_minute ({self.audio_seconds_per_minute:g}) "
                f"is below max_clip_seconds ({self.max_clip_seconds:g}): a clip that long could "
                "never fit a device's audio budget."
            )
        return self
