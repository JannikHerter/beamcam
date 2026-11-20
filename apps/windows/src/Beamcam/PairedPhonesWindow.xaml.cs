using System.Globalization;
using System.Windows;
using System.Windows.Controls;

namespace Beamcam.App;

public sealed record PairedRow(PairedPhone Phone, string Name, string Subtitle)
{
    public string ForgetLabel => Text.Format("ForgetPhone", Name);

    // What screen readers say for the row; the record's own text would include the fingerprint.
    public override string ToString() => Name;
}

public partial class PairedPhonesWindow : Window
{
    readonly Config _config;
    readonly Action<IReadOnlyList<PairedPhone>, string> _forget;

    public PairedPhonesWindow(Config config, Action<IReadOnlyList<PairedPhone>, string> forget)
    {
        InitializeComponent();
        _config = config;
        _forget = forget;
        Render();
    }

    static string LastConnected(long seconds)
    {
        if (seconds <= 0)
            return "";
        var when = DateTimeOffset.FromUnixTimeSeconds(seconds).LocalDateTime;
        return when.Date == DateTime.Today
            ? Text.Format("LastConnectedToday", when.ToString("t", CultureInfo.CurrentCulture))
            : Text.Format("LastConnectedOn", when.ToString("d", CultureInfo.CurrentCulture));
    }

    void Render()
    {
        var phones = _config.PairedPhones();
        List.ItemsSource = phones.Select(p => new PairedRow(p, p.Name, LastConnected(p.LastConnected))).ToList();
        var none = phones.Count == 0;
        Empty.Visibility = none ? Visibility.Visible : Visibility.Collapsed;
        Intro.Visibility = none ? Visibility.Collapsed : Visibility.Visible;
        ForgetAll.Visibility = phones.Count > 1 ? Visibility.Visible : Visibility.Collapsed;
    }

    void OnForget(object sender, RoutedEventArgs e)
    {
        if (((Button)sender).Tag is not PairedRow row)
            return;
        _forget([row.Phone], Text.Format("PhoneForgotten", row.Name));
        Render();
    }

    void OnForgetAll(object sender, RoutedEventArgs e)
    {
        _forget(_config.PairedPhones(), Text.Get("PhonesForgotten"));
        Render();
    }
}
