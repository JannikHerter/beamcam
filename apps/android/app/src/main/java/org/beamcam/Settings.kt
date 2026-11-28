package org.beamcam

import android.content.Context
import android.os.Build
import android.provider.Settings as AndroidSettings
import androidx.core.content.edit
import java.util.UUID

/** Small persistent settings: this phone's id, trusted computers, preferred camera, updates. */
class Settings(context: Context) {
    private val prefs = context.getSharedPreferences("beamcam", Context.MODE_PRIVATE)
    private val appContext = context.applicationContext

    val deviceId: String
        get() = prefs.getString(KEY_DEVICE_ID, null) ?: UUID.randomUUID().toString().also {
            prefs.edit { putString(KEY_DEVICE_ID, it) }
        }

    val deviceName: String
        get() = AndroidSettings.Global.getString(appContext.contentResolver, AndroidSettings.Global.DEVICE_NAME)
            ?.trim()
            ?.takeIf { it.isNotEmpty() }
            ?: Build.MODEL

    var facing: Facing
        get() = runCatching { Facing.valueOf(prefs.getString(KEY_FACING, null) ?: "") }.getOrDefault(Facing.FRONT)
        set(value) = prefs.edit { putString(KEY_FACING, value.name) }

    /** Whether the app may look for new versions; null until the user answered. */
    var checkUpdates: Boolean?
        get() = if (prefs.contains(KEY_CHECK_UPDATES)) prefs.getBoolean(KEY_CHECK_UPDATES, false) else null
        set(value) = prefs.edit { if (value == null) remove(KEY_CHECK_UPDATES) else putBoolean(KEY_CHECK_UPDATES, value) }

    /** Newest first. Read and written from session threads and the UI, hence [lock]. */
    fun allowedComputers(): List<AllowedComputer> = synchronized(lock) {
        AllowedComputers.decode(prefs.getString(KEY_COMPUTERS, null), prefs.getStringSet(KEY_ALLOWED, emptySet()).orEmpty())
    }

    fun isAllowed(secretHash: String): Boolean = allowedComputers().any { it.secretHash == secretHash }

    /** Allows [secretHash], or notes that it connected again. */
    fun connected(secretHash: String, name: String) = update {
        AllowedComputers.connected(it, secretHash, name, System.currentTimeMillis())
    }

    fun forgetComputer(secretHash: String) = update { AllowedComputers.without(it, secretHash) }

    fun forgetComputers() = update { emptyList() }

    private fun update(transform: (List<AllowedComputer>) -> List<AllowedComputer>) = synchronized(lock) {
        val computers = transform(allowedComputers())
        prefs.edit {
            putString(KEY_COMPUTERS, AllowedComputers.encode(computers))
            remove(KEY_ALLOWED)
            remove(KEY_LEGACY_TRUSTED)
        }
    }

    private companion object {
        const val KEY_DEVICE_ID = "device_id"
        const val KEY_COMPUTERS = "allowed_computers"
        // Before names were stored: a plain set of secret hashes, migrated on the next change.
        const val KEY_ALLOWED = "allowed_secret_hashes"
        // Protocol 1 trusted plain client ids; they grant nothing now and are dropped on the next change.
        const val KEY_LEGACY_TRUSTED = "trusted_clients"
        const val KEY_FACING = "facing"
        const val KEY_CHECK_UPDATES = "check_updates"
        // Settings objects in the service and the activity share one file.
        val lock = Any()
    }
}
