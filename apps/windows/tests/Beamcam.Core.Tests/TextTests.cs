using System.Globalization;
using System.Text.RegularExpressions;
using System.Xml.Linq;

namespace Beamcam.Tests;

/// <summary>Every text the Windows app shows exists in every language, with the same placeholders.</summary>
public partial class TextTests
{
    static readonly string Source = FindSource();

    static string FindSource()
    {
        for (var dir = new DirectoryInfo(AppContext.BaseDirectory); dir is not null; dir = dir.Parent)
            if (File.Exists(Path.Combine(dir.FullName, "Beamcam.slnx")))
                return Path.Combine(dir.FullName, "src");
        throw new DirectoryNotFoundException("apps/windows/src");
    }

    static Dictionary<string, string> Read(string file) =>
        XDocument.Load(Path.Combine(Source, "Beamcam.Core", file)).Root!.Elements("data")
            .ToDictionary(d => (string)d.Attribute("name")!, d => (string)d.Element("value")!);

    static IEnumerable<string> Placeholders(string text) => PlaceholderPattern().Matches(text).Select(m => m.Value).Order();

    [GeneratedRegex(@"\{\d+\}")]
    private static partial Regex PlaceholderPattern();

    // Text.Get("Key"), Text.Format("Key", …), Text.Get(cond ? "A" : "B") and {app:Tr Key} in XAML.
    [GeneratedRegex(@"Text\.(?:Get|Format)\(([^;]*)")]
    private static partial Regex CallPattern();

    // Keys are PascalCase, which keeps format arguments such as "t" out.
    [GeneratedRegex(@"""([A-Z]\w*)""")]
    private static partial Regex KeyLiteral();

    [GeneratedRegex(@"\{app:Tr (\w+)\}")]
    private static partial Regex XamlPattern();

    [Fact]
    public void EveryTextHasAGermanVersionWithTheSamePlaceholders()
    {
        var english = Read("Strings.resx");
        var german = Read("Strings.de.resx");
        Assert.True(english.Count > 50);
        Assert.Empty(english.Keys.Except(german.Keys).Order());   // untranslated
        Assert.Empty(german.Keys.Except(english.Keys).Order());   // no longer in English
        foreach (var (key, text) in english)
        {
            Assert.False(string.IsNullOrWhiteSpace(german[key]), key);
            Assert.True(Placeholders(text).SequenceEqual(Placeholders(german[key])), $"{key}: placeholders differ");
        }
    }

    [Fact]
    public void EveryKeyInTheCodeExistsAndEveryTextIsUsed()
    {
        var english = Read("Strings.resx");
        var asked = new HashSet<string>();
        var literals = new HashSet<string>();
        foreach (var file in Directory.EnumerateFiles(Source, "*.*", SearchOption.AllDirectories)
                     .Where(f => f.EndsWith(".cs") || f.EndsWith(".xaml"))
                     .Where(f => !f.Contains($"{Path.DirectorySeparatorChar}obj{Path.DirectorySeparatorChar}")))
        {
            var code = File.ReadAllText(file);
            foreach (Match call in CallPattern().Matches(code))
                foreach (Match key in KeyLiteral().Matches(call.Groups[1].Value))
                    asked.Add(key.Groups[1].Value);
            foreach (Match tr in XamlPattern().Matches(code))
                asked.Add(tr.Groups[1].Value);
            foreach (Match literal in KeyLiteral().Matches(code))
                literals.Add(literal.Groups[1].Value);
        }
        Assert.Empty(asked.Where(k => !english.ContainsKey(k)).Order()); // asked for but missing
        Assert.Empty(english.Keys.Where(k => !asked.Contains(k) && !literals.Contains(k)).Order()); // never used
    }

    [Fact]
    public void GermanIsUsedForAGermanWindows()
    {
        var previous = CultureInfo.CurrentUICulture;
        try
        {
            CultureInfo.CurrentUICulture = CultureInfo.GetCultureInfo("de-AT");
            Assert.Equal("Das Handy hat diesen Computer nicht zugelassen.", EndReasons.Message("denied"));
            Assert.Equal("Die Übertragung wurde beendet (whatever).", EndReasons.Message("whatever"));
            CultureInfo.CurrentUICulture = CultureInfo.GetCultureInfo("fr-FR");
            Assert.Equal("The phone did not allow this computer.", EndReasons.Message("denied"));
        }
        finally
        {
            CultureInfo.CurrentUICulture = previous;
        }
    }

    [Fact]
    public void EveryTranslationIsOffered()
    {
        var files = Directory.EnumerateFiles(Path.Combine(Source, "Beamcam.Core"), "Strings.*.resx")
            .Select(f => Path.GetFileName(f).Split('.')[1]).Append("en");
        Assert.Equal(files.Order(), Text.Languages.Keys.Order());
    }
}
