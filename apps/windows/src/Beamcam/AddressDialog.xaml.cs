using System.Windows;

namespace Beamcam.App;

public partial class AddressDialog : Window
{
    public AddressDialog(string? last)
    {
        InitializeComponent();
        Input.TextChanged += (_, _) => Placeholder.Visibility = Input.Text.Length == 0 ? Visibility.Visible : Visibility.Collapsed;
        Input.Text = last ?? "";
        Input.SelectAll();
        Loaded += (_, _) => Input.Focus();
    }

    public string Address => Input.Text;

    void OnConnect(object sender, RoutedEventArgs e) => DialogResult = true;
}
