/*
 * The notification a push shows: "something is waiting", and nothing more.
 *
 * A push notice carries neither content nor kind and is padded to one length (ADR-0042), so there
 * is nothing to show but a fixed sentence. Tapping it opens the app, which fetches the item over
 * its own signed session. The notification is deliberately identical for a question, an Alarm and a
 * reply, so the lock screen learns nothing.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `android/`. Called by PushReceiver when a push arrives.
 *
 * Key invariants:
 *   - The text is a constant; no push payload is ever read or displayed.
 *   - Visibility is secret on the lock screen and the notice cancels itself when tapped.
 *   - A missing notification permission is silent: the push is dropped, never retried or logged.
 *
 * See Also:
 *   - docs/entrance/landing-board.md section 9 (the push contract).
 */
package dev.hivemind.observation.push

import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat
import dev.hivemind.observation.MainActivity
import dev.hivemind.observation.R

object WaitingNotice {
    private const val CHANNEL = "hive_waiting"
    private const val NOTICE_ID = 4101

    fun post(context: Context) {
        val manager = NotificationManagerCompat.from(context)
        if (!manager.areNotificationsEnabled()) return
        // Channels are idempotent: creating an existing one changes nothing.
        manager.createNotificationChannel(
            NotificationChannel(CHANNEL, "Waiting in the Hive", NotificationManager.IMPORTANCE_DEFAULT),
        )
        val open = PendingIntent.getActivity(
            context,
            0,
            Intent(context, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
        )
        val notice = NotificationCompat.Builder(context, CHANNEL)
            .setSmallIcon(R.drawable.ic_stat_hive)
            .setContentTitle("Observation Hive")
            .setContentText("Something is waiting.")
            .setVisibility(NotificationCompat.VISIBILITY_SECRET)
            .setContentIntent(open)
            .setAutoCancel(true)
            .build()
        try {
            manager.notify(NOTICE_ID, notice)
        } catch (denied: SecurityException) {
            // The permission was withdrawn between the check and the post; there is nothing to do.
        }
    }
}
