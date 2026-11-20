package org.beamcam

import android.app.Activity
import android.app.LocaleManager
import android.content.Context
import android.content.res.Configuration
import android.content.res.Resources
import android.os.Build
import android.os.LocaleList
import androidx.core.content.edit
import java.util.Locale

/**
 * The app's language: the phone's, or one the user picked. Android 13+ stores the choice itself
 * (it also shows up in Settings → Apps → Beamcam → Language). Before that, the app keeps it and
 * applies it in [wrap].
 */
object AppLanguage {
    /** Each language's name in that language. Keep in sync with res/xml/locales_config.xml. */
    val SUPPORTED = linkedMapOf("en" to "English", "de" to "Deutsch")

    private const val PREFS = "beamcam"
    private const val KEY = "language"

    /** The chosen language tag, or null for the phone's language. */
    fun chosen(context: Context): String? =
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            context.getSystemService(LocaleManager::class.java).applicationLocales
                .takeUnless { it.isEmpty }?.get(0)?.language?.takeIf { it in SUPPORTED }
        } else {
            context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).getString(KEY, null)
        }

    fun choose(activity: Activity, tag: String?) {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            // Android recreates the activity and updates every context of the app.
            activity.getSystemService(LocaleManager::class.java).applicationLocales =
                if (tag == null) LocaleList.getEmptyLocaleList() else LocaleList.forLanguageTags(tag)
        } else {
            activity.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit {
                if (tag == null) remove(KEY) else putString(KEY, tag)
            }
            activity.recreate()
        }
    }

    /** For attachBaseContext of the activity and the service; only needed before Android 13. */
    fun wrap(base: Context): Context {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) return base
        val tag = base.getSharedPreferences(PREFS, Context.MODE_PRIVATE).getString(KEY, null) ?: return base
        val config = Configuration(base.resources.configuration).apply { setLocale(Locale.forLanguageTag(tag)) }
        return base.createConfigurationContext(config)
    }

    /** The phone's own language among ours, for "Same as this phone (…)". */
    fun phoneLanguageName(context: Context): String {
        // From Android 13 the app's own choice also shows up in Resources.getSystem().
        val phone = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            context.getSystemService(LocaleManager::class.java).systemLocales
        } else {
            Resources.getSystem().configuration.locales
        }
        return (0 until phone.size()).firstNotNullOfOrNull { SUPPORTED[phone[it].language] } ?: SUPPORTED.getValue("en")
    }
}
