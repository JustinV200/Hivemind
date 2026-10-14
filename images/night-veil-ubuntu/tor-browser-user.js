// tor-browser-user.js (roadmap step 5.3a): Tor Browser's WebRTC switched off in its own profile.
//
// Copied by the Dockerfile to Browser/TorBrowser/Data/Browser/profile.default/user.js, which the
// browser reads on every start after prefs.js, so this setting wins whatever the browser saved
// before. WebRTC lets a page learn the machine's own addresses (the local-IP leak); a Night Veil
// Cell is location-blind (codingrules section 8.7), so the browser must not be able to. Attestation
// does not drive the browser to try a leak: it reads this file and the profile's prefs.js
// (hivemind.hive.night_veil.judges.webrtc) and requires this exact setting to be false, so a
// release that dropped it, or an image that lost this file, fails the Cell before it is ready.
user_pref("media.peerconnection.enabled", false);
