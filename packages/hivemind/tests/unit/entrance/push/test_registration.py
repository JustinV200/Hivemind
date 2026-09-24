"""Tests for hivemind.entrance.push.registration: who may register a push subscription.

Fits into the Hive:
    Mirrors src/hivemind/entrance/push/registration.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.entrance.push.registration for the module under test.
"""

from __future__ import annotations

import pytest
from builders.entrance import make_device
from unit.entrance.push.support import (
    HOOK_URL,
    PUSH_URL,
    StaticResolver,
    SteppingClock,
    UserAgent,
    approved_device,
    make_guard,
)

from hivemind.entrance.enrol import DeviceStatus
from hivemind.entrance.push import (
    Admission,
    ChannelKind,
    DestinationRefusal,
    DestinationRefusedError,
    PushRegistrationRefusedError,
    registration_refusal,
)
from hivemind.manifest.schema import EntrancePushSection

_OFFERED = EntrancePushSection()  # Both channels on: the manifest's default.


def test_an_approved_device_holding_entrance_push_may_register_either_channel() -> None:
    device = approved_device(SteppingClock())

    webhook = registration_refusal(device, ChannelKind.WEBHOOK, None, _OFFERED)
    web_push = registration_refusal(device, ChannelKind.WEB_PUSH, UserAgent().keys, _OFFERED)

    assert webhook is None
    assert web_push is None


@pytest.mark.parametrize("status", [DeviceStatus.PENDING, DeviceStatus.LOCKED])
def test_a_device_that_is_not_approved_may_not_register(status: DeviceStatus) -> None:
    device = make_device(SteppingClock(), status)

    refusal = registration_refusal(device, ChannelKind.WEBHOOK, None, _OFFERED)

    assert refusal == f"it is {status.name}, not APPROVED"


def test_a_device_without_entrance_push_may_not_register() -> None:
    device = approved_device(SteppingClock(), "entrance:submit", "entrance:answer")

    refusal = registration_refusal(device, ChannelKind.WEBHOOK, None, _OFFERED)

    assert refusal == "it does not hold entrance:push"


@pytest.mark.parametrize(
    ("channel", "settings"),
    [
        (ChannelKind.WEBHOOK, EntrancePushSection(webhooks=False)),
        (ChannelKind.WEB_PUSH, EntrancePushSection(web_push=False)),
    ],
)
def test_a_channel_the_manifest_turns_off_is_refused(
    channel: ChannelKind, settings: EntrancePushSection
) -> None:
    keys = UserAgent().keys if channel is ChannelKind.WEB_PUSH else None

    refusal = registration_refusal(approved_device(SteppingClock()), channel, keys, settings)

    assert refusal == f"[entrance.push] does not offer the {channel.value} channel"


def test_keys_must_fit_the_channel() -> None:
    device = approved_device(SteppingClock())

    without_keys = registration_refusal(device, ChannelKind.WEB_PUSH, None, _OFFERED)
    with_keys = registration_refusal(device, ChannelKind.WEBHOOK, UserAgent().keys, _OFFERED)

    assert without_keys is not None
    assert with_keys is not None
    assert "p256dh and auth" in without_keys


async def test_admit_passes_a_https_webhook_and_a_push_service_endpoint() -> None:
    admission = Admission(_OFFERED, make_guard())
    device = approved_device(SteppingClock())

    await admission.admit(device, ChannelKind.WEBHOOK, HOOK_URL, None)
    await admission.admit(device, ChannelKind.WEB_PUSH, PUSH_URL, UserAgent().keys)


async def test_admit_raises_the_rule_that_refuses() -> None:
    admission = Admission(_OFFERED, make_guard())
    device = make_device(SteppingClock(), DeviceStatus.LOCKED)

    with pytest.raises(PushRegistrationRefusedError, match="LOCKED") as caught:
        await admission.admit(device, ChannelKind.WEBHOOK, HOOK_URL, None)

    assert caught.value.device_id == device.id


async def test_admit_refuses_a_webhook_aimed_at_the_loopback_listener() -> None:
    admission = Admission(_OFFERED, make_guard())

    with pytest.raises(DestinationRefusedError) as caught:
        await admission.admit(
            approved_device(SteppingClock()), ChannelKind.WEBHOOK, "https://127.0.0.1:8710/", None
        )

    assert caught.value.reason is DestinationRefusal.LOOPBACK


async def test_admit_applies_the_guard_to_web_push_endpoints_too() -> None:
    resolver = StaticResolver({"metadata.example.net": ["169.254.169.254"]})
    admission = Admission(_OFFERED, make_guard(resolver))
    endpoint = "https://metadata.example.net/latest"

    with pytest.raises(DestinationRefusedError) as caught:
        await admission.admit(
            approved_device(SteppingClock()), ChannelKind.WEB_PUSH, endpoint, UserAgent().keys
        )

    assert caught.value.reason is DestinationRefusal.LINK_LOCAL


async def test_admit_refuses_a_plain_http_web_push_endpoint_even_inside_the_vpn() -> None:
    resolver = StaticResolver({"distributor.tailnet.example": ["100.100.1.2"]})
    admission = Admission(_OFFERED, make_guard(resolver))
    endpoint = "http://distributor.tailnet.example/up"

    with pytest.raises(PushRegistrationRefusedError, match="always https"):
        await admission.admit(
            approved_device(SteppingClock()), ChannelKind.WEB_PUSH, endpoint, UserAgent().keys
        )
