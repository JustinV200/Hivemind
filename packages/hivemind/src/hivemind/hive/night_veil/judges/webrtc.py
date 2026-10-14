"""Judge whether a Night Veil Cell's browser profile switches WebRTC off.

WebRTC lets a page in the browser learn the machine's own addresses (the local-IP leak), which on a
Night Veil Cell would put the Cell's real interfaces in the hands of whatever site a task visits.
The attestation (roadmap step 5.7b) cannot drive the browser to try a leak, so it does what is
deterministic and read-only: the Tor Browser the image carries has a profile, and the profile either
sets `media.peerconnection.enabled` to false or it does not. Firefox-based browsers read two files
of preferences at start, `prefs.js` (what the browser itself saved) and then `user.js` (what the
image's builder wrote), and `user.js` wins whatever `prefs.js` says, so that is the order the judge
reads them in: the last setting in `user.js`, else the last in `prefs.js`. A profile that never
mentions the preference fails, even though Tor Browser's own defaults may switch WebRTC off today:
the attestation checks what the image fixed, not what a browser release happens to default to.

Fits into the Hive:
    Layer 3 (sources of Cells), inside `hive.night_veil.judges`. Called by `hive.night_veil.
    session_probe.SessionNightVeilProbe` with the text of the profile's two files. Calls into
    `hive.night_veil.results` and the standard library only.

Key invariants:
    - Pure and total: the same text always gives the same result, and nothing raises.
    - Green needs an explicit, effective `media.peerconnection.enabled` of false; an absent or
      unreadable setting is red, never a pass.
    - A commented-out setting is not a setting.
    - A result's `detail` is a fixed word; the profile's text is never put in it.

See Also:
    - images/night-veil-ubuntu/tor-browser-user.js for the profile setting this reads.
    - hivemind.hive.night_veil.session_probe for the check that reads the two files.
"""

from __future__ import annotations

import re

from hivemind.hive.night_veil.results import CheckResult, verdict

WEBRTC_PREF = "media.peerconnection.enabled"  # The preference that switches WebRTC on or off.
# `user_pref("name", false);` in a profile's files, or `pref(...)` as a default file spells it.
_SETTING = re.compile(
    rf"""(?:user_)?pref\(\s*["']{re.escape(WEBRTC_PREF)}["']\s*,\s*(true|false)\s*\)"""
)
_COMMENT_STARTS = ("//", "/*", "*", "#")  # A line that begins so is a comment, whatever it quotes.

__all__ = ["WEBRTC_PREF", "judge_webrtc", "webrtc_setting"]


def judge_webrtc(user_js: str, prefs_js: str) -> CheckResult:
    """Judge that the browser profile explicitly switches WebRTC off.

    Args:
        user_js: The text of the profile's `user.js`; "" if there is none or it could not be read.
        prefs_js: The text of the profile's `prefs.js`; "" likewise.

    Returns:
        PASS when the effective setting is false; FAIL when it is true, or neither file sets it.
    """
    # The browser applies user.js after prefs.js on every start, so its word is the effective one.
    setting = webrtc_setting(user_js)
    if setting is None:
        setting = webrtc_setting(prefs_js)
    if setting is None:
        return verdict(passed=False, detail="pref_not_set")
    if setting:
        return verdict(passed=False, detail="webrtc_enabled")
    return verdict(passed=True, detail="webrtc_disabled")


def webrtc_setting(text: str) -> bool | None:
    """Return the last value `text` gives `media.peerconnection.enabled`, or None if it never does.

    Args:
        text: A preferences file: `user_pref("name", value);` lines, possibly among comments.

    Returns:
        True or False for the last setting in the file (a later line overrides an earlier one, as
        the browser reads them), or None when no uncommented line sets it.
    """
    value: bool | None = None
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith(_COMMENT_STARTS):
            continue  # A commented-out setting is not a setting.
        for match in _SETTING.finditer(stripped):
            value = match.group(1) == "true"
    return value
