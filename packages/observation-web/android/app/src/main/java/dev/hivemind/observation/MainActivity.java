/*
 * The app's one activity: a Capacitor bridge with the Hive's three native plugins registered.
 *
 * Capacitor discovers official plugins by itself; the plugins in this project are local classes and
 * must be registered before the bridge is created. Everything else about the activity (the WebView,
 * the pinned origin, the App Link and share intents) comes from Capacitor and AndroidManifest.xml.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `android/`. Launched by the system for the launcher icon, for an
 *   App Link on the Entrance's `/enrol` and for the share sheet.
 *
 * Key invariants:
 *   - Registration happens before `super.onCreate`, which is when the bridge reads its plugins.
 *   - No logic lives here; each plugin owns one concern (passkeys, UnifiedPush, share intake).
 *
 * See Also:
 *   - capacitor.config.ts for the pinned origin and android/README.md for how to build.
 */
package dev.hivemind.observation;

import android.os.Bundle;
import com.getcapacitor.BridgeActivity;
import dev.hivemind.observation.passkey.PasskeyPlugin;
import dev.hivemind.observation.push.UnifiedPushPlugin;
import dev.hivemind.observation.share.ShareIntakePlugin;

public class MainActivity extends BridgeActivity {
    @Override
    public void onCreate(Bundle savedInstanceState) {
        registerPlugin(PasskeyPlugin.class);
        registerPlugin(UnifiedPushPlugin.class);
        registerPlugin(ShareIntakePlugin.class);
        super.onCreate(savedInstanceState);
    }
}
