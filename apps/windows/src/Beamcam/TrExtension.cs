using System.Windows.Markup;

namespace Beamcam.App;

/// <summary>A translated text in XAML: <c>Content="{app:Tr Connect}"</c>. Texts are looked up once;
/// switching the language restarts the window.</summary>
[MarkupExtensionReturnType(typeof(string))]
public sealed class TrExtension(string key) : MarkupExtension
{
    public string Key { get; set; } = key;

    public override object ProvideValue(IServiceProvider serviceProvider) => Text.Get(Key);
}
