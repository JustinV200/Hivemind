"""Tests for hivemind.hive.night_veil.judges.webrtc: does the browser profile switch WebRTC off.

The attestation could not drive a browser to try a leak, so `webrtc_leak_blocked` was always
`NOT_APPLICABLE`, which counts as a pass. It now reads the Tor Browser profile's two preference
files: `user.js` wins over `prefs.js` (the browser applies it last), the last setting in a file
wins, a commented-out line is not a setting, and a profile that never mentions the preference is
red. These tests feed the judge the text such files hold.

Fits into the Hive:
    Mirrors src/hivemind/hive/night_veil/judges/webrtc.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.hive.night_veil.judges.webrtc for the module under test.
    - images/night-veil-ubuntu/tor-browser-user.js for the setting the image bakes.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hivemind.hive.night_veil.judges import WEBRTC_PREF, judge_webrtc, webrtc_setting
from hivemind.hive.night_veil.results import CheckStatus

_OFF = 'user_pref("media.peerconnection.enabled", false);\n'
_ON = 'user_pref("media.peerconnection.enabled", true);\n'
_IMAGE_USER_JS = (
    Path(__file__).resolve().parents[7] / "images/night-veil-ubuntu/tor-browser-user.js"
)


def _verdict(user_js: str, prefs_js: str = "") -> tuple[CheckStatus, str]:
    result = judge_webrtc(user_js, prefs_js)
    return result.status, result.detail


def test_a_user_js_that_switches_webrtc_off_is_green() -> None:
    assert _verdict(_OFF) == (CheckStatus.PASS, "webrtc_disabled")


def test_a_prefs_js_that_switches_it_off_is_green_when_there_is_no_user_js() -> None:
    assert _verdict("", _OFF) == (CheckStatus.PASS, "webrtc_disabled")


def test_user_js_wins_over_prefs_js_both_ways() -> None:
    assert _verdict(_ON, _OFF) == (CheckStatus.FAIL, "webrtc_enabled")
    assert _verdict(_OFF, _ON) == (CheckStatus.PASS, "webrtc_disabled")


def test_a_profile_that_enables_webrtc_is_red() -> None:
    assert _verdict(_ON) == (CheckStatus.FAIL, "webrtc_enabled")


@pytest.mark.parametrize("user_js", ["", "\n", 'user_pref("browser.startup.page", 0);\n'])
def test_a_profile_that_never_mentions_the_setting_is_red_not_applicable_nor_green(
    user_js: str,
) -> None:
    # A browser release's own default may be off, but the attestation checks what the image fixed.
    assert _verdict(user_js, user_js) == (CheckStatus.FAIL, "pref_not_set")


def test_the_last_setting_in_a_file_is_the_one_in_force() -> None:
    assert _verdict(_ON + _OFF)[0] is CheckStatus.PASS
    assert _verdict(_OFF + _ON)[0] is CheckStatus.FAIL


@pytest.mark.parametrize(
    "line",
    [
        '// user_pref("media.peerconnection.enabled", false);',
        '  /* user_pref("media.peerconnection.enabled", false); */',
        ' * user_pref("media.peerconnection.enabled", false);',
        '# user_pref("media.peerconnection.enabled", false);',
    ],
)
def test_a_commented_out_setting_is_not_a_setting(line: str) -> None:
    assert _verdict(line + "\n") == (CheckStatus.FAIL, "pref_not_set")


@pytest.mark.parametrize(
    "line",
    [
        'pref("media.peerconnection.enabled", false);',
        "user_pref('media.peerconnection.enabled', false);",
        'user_pref( "media.peerconnection.enabled" , false ) ;',
        '\tuser_pref("media.peerconnection.enabled",false);',
    ],
)
def test_the_spellings_a_preference_file_uses_are_all_read(line: str) -> None:
    assert webrtc_setting(line + "\n") is False


def test_another_peerconnection_setting_is_not_the_one_that_switches_it_off() -> None:
    other = 'user_pref("media.peerconnection.ice.default_address_only", true);\n'

    assert webrtc_setting(other) is None
    assert webrtc_setting('user_pref("media.peerconnection.enabled.extra", false);\n') is None


def test_the_preference_the_judge_reads_is_the_one_the_image_bakes() -> None:
    # The image's file and the judge must agree, or a built Cell would fail its own attestation.
    baked = _IMAGE_USER_JS.read_text(encoding="utf-8")

    assert WEBRTC_PREF in baked and webrtc_setting(baked) is False
    assert judge_webrtc(baked, "").status is CheckStatus.PASS
