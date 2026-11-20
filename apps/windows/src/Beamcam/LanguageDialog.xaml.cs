using System.Windows;
using System.Windows.Controls;

namespace Beamcam.App;

public partial class LanguageDialog : Window
{
    /// <summary>The chosen language code, or null for the computer's language.</summary>
    public string? Chosen { get; private set; }

    public LanguageDialog(string? current)
    {
        InitializeComponent();
        Chosen = current;
        var options = new List<(string? Code, string Label)> { (null, Text.Format("SameAsComputer", Text.Languages[Text.SystemLanguage()])) };
        options.AddRange(Text.Languages.Select(l => ((string?)l.Key, l.Value)));
        foreach (var (code, label) in options)
        {
            var choice = new RadioButton { Content = label, GroupName = "language", IsChecked = code == current, Margin = new Thickness(0, 0, 0, 6) };
            choice.Checked += (_, _) => Chosen = code;
            Choices.Children.Add(choice);
        }
    }

    void OnRestart(object sender, RoutedEventArgs e) => DialogResult = true;
}
