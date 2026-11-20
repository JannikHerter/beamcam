using System.Globalization;
using System.Resources;

namespace Beamcam;

/// <summary>Translated texts from Strings.resx (English) and Strings.&lt;language&gt;.resx. The window
/// follows the Windows display language unless the user picked another; the command line runs with
/// the invariant culture and so stays English, because scripts read it.</summary>
public static class Text
{
    static readonly ResourceManager Resources = new("Beamcam.Strings", typeof(Text).Assembly);
    // The display language as Windows set it, before Apply() changes the process's culture.
    static readonly CultureInfo SystemCulture = CultureInfo.CurrentUICulture;

    /// <summary>Each language's name in that language, as the language menu shows it.</summary>
    public static readonly IReadOnlyDictionary<string, string> Languages = new Dictionary<string, string>
    {
        ["en"] = "English",
        ["de"] = "Deutsch",
    };

    public static string Get(string key) => Resources.GetString(key, CultureInfo.CurrentUICulture) ?? key;

    public static string Format(string key, params object?[] args) => string.Format(CultureInfo.CurrentCulture, Get(key), args);

    /// <summary>The first supported language of Windows' display language, else English.</summary>
    public static string SystemLanguage()
    {
        for (var culture = SystemCulture; !string.IsNullOrEmpty(culture.Name); culture = culture.Parent)
            if (Languages.ContainsKey(culture.TwoLetterISOLanguageName))
                return culture.TwoLetterISOLanguageName;
        return "en";
    }

    /// <summary>Switches texts to <paramref name="language"/>, or to the system's when it is null or
    /// unknown. Call before any window exists. Returns the language used.</summary>
    public static string Apply(string? language)
    {
        var code = language is not null && Languages.ContainsKey(language) ? language : SystemLanguage();
        // Keep the system's regional formats (dates, times) when its language is the one shown.
        var culture = code == SystemLanguage() && SystemCulture.TwoLetterISOLanguageName == code ? SystemCulture : CultureInfo.GetCultureInfo(code);
        CultureInfo.DefaultThreadCurrentUICulture = culture;
        CultureInfo.CurrentUICulture = culture;
        return code;
    }
}
