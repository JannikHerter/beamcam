using System.ComponentModel;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Interop;
using System.Windows.Media;
using System.Windows.Media.Imaging;
using System.Windows.Threading;

namespace Beamcam.App;

/// <summary>A phone in the list.</summary>
public sealed record PhoneRow(Phone Phone, string Name, string Subtitle)
{
    public string ConnectLabel => Text.Format("ConnectTo", Name);

    // What screen readers say for the row.
    public override string ToString() => Name;
}

/// <summary>The window, like apps/linux/src/beamcam/ui.py: it only renders state and starts or stops
/// the controller; sessions, discovery and reconnect rules live in Beamcam.Core.</summary>
public partial class MainWindow : Window, IControllerEvents
{
    readonly Config _config = Config.Load();
    readonly Reconnector _reconnector = new();
    readonly MdnsBrowser _browser;
    readonly DispatcherTimer _reconnectTimer = new();
    readonly DispatcherTimer _toastTimer = new() { Interval = TimeSpan.FromSeconds(6) };
    readonly VirtualCamera? _camera;
    readonly WriteableBitmap _preview = new(NativeVideoOutput.PreviewWidth, NativeVideoOutput.PreviewHeight, 96, 96, PixelFormats.Bgra32, null);
    List<Phone> _phones = [];
    Controller? _controller;
    bool _autoConnectAllowed = true;
    bool _closing;
    string _facing = "front";
    long _previewSeen;

    public MainWindow()
    {
        InitializeComponent();
        Preview.Source = _preview;
        _reconnectTimer.Tick += (_, _) => Reconnect();
        _toastTimer.Tick += (_, _) =>
        {
            _toastTimer.Stop();
            Toast.Visibility = Visibility.Collapsed;
        };
        _camera = VirtualCamera.TryCreate(out var problem);
        if (_camera is null)
            ShowBanner(problem!);
        else
            _camera.SetIdleFrame(IdleFrame.Render(), VirtualCamera.Width * 4);
        RenderPhones();
        _browser = new MdnsBrowser(phones => Dispatcher.BeginInvoke(() => OnPhones(phones)));
        _browser.Start();
    }

    public void BringToFront()
    {
        if (WindowState == WindowState.Minimized)
            WindowState = WindowState.Normal;
        Activate();
    }

    void Post(Action action) => Dispatcher.BeginInvoke(() =>
    {
        if (!_closing)
            action();
    });

    // Pages

    void ShowPage(FrameworkElement page)
    {
        foreach (var p in new FrameworkElement[] { PhonesPage, ConnectingPage, StreamingPage })
            p.Visibility = p == page ? Visibility.Visible : Visibility.Collapsed;
    }

    void ShowConnecting(string title, string? description = null, string? code = null)
    {
        ConnectingTitle.Text = title;
        ConnectingDescription.Text = description ?? "";
        ConnectingDescription.Visibility = description is null ? Visibility.Collapsed : Visibility.Visible;
        ConnectingCode.Text = code ?? "";
        ConnectingCode.Visibility = code is null ? Visibility.Collapsed : Visibility.Visible;
        ShowPage(ConnectingPage);
    }

    void ShowPhones()
    {
        ShowPage(PhonesPage);
        _browser.Refresh();
    }

    void ShowBanner(string text)
    {
        BannerText.Text = text;
        Banner.Visibility = Visibility.Visible;
    }

    public void ShowToast(string text)
    {
        ToastText.Text = text;
        Toast.Visibility = Visibility.Visible;
        _toastTimer.Stop();
        _toastTimer.Start();
    }

    // Discovery

    void OnPhones(List<Phone> phones)
    {
        if (_closing)
            return;
        _phones = phones;
        RenderPhones();
        if (_reconnector.Phones(phones) && _reconnectTimer.IsEnabled)
        {
            // The phone is back at a new address: no need to wait for the timer.
            Reconnect();
        }
        var last = _config.LastPhoneId;
        if (_controller is null && _autoConnectAllowed && last is not null && _config.IsPaired(last)
            && phones.FirstOrDefault(p => p.Id == last) is { } remembered)
            ConnectTo(remembered.Host, remembered.Port, remembered.Name, remembered.Id, user: false);
    }

    public void RenderPhones()
    {
        PhoneList.ItemsSource = _phones
            .Select(p => new PhoneRow(p, p.Name, _config.IsPaired(p.Id) ? Text.Format("PairedAddress", p.Host) : p.Host))
            .ToList();
        var found = _phones.Count > 0;
        PhoneList.Visibility = found ? Visibility.Visible : Visibility.Collapsed;
        Searching.Visibility = found ? Visibility.Collapsed : Visibility.Visible;
        if (found)
        {
            PhonesTitle.Text = Text.Get("ChooseYourPhone");
            PhonesDescription.Visibility = Visibility.Collapsed;
        }
        else
        {
            PhonesTitle.Text = Text.Get("LookingForYourPhone");
            PhonesDescription.Text = Text.Get("OpenBeamcamOnPhone") + "\n" + Text.Get("SameWifi");
            PhonesDescription.Visibility = Visibility.Visible;
        }
    }

    // Connecting and streaming

    void OnConnectPhone(object sender, RoutedEventArgs e)
    {
        if (((Button)sender).Tag is PhoneRow row)
            ConnectTo(row.Phone.Host, row.Phone.Port, row.Phone.Name, row.Phone.Id);
    }

    void OnConnectByAddress(object sender, RoutedEventArgs e)
    {
        var dialog = new AddressDialog(_config.LastAddress) { Owner = this };
        if (dialog.ShowDialog() != true)
            return;
        var text = dialog.Address;
        (string Host, int Port) target;
        try
        {
            target = Address.Parse(text);
        }
        catch (FormatException)
        {
            ShowToast(Text.Format("NotAnAddress", "192.168.1.23"));
            return;
        }
        _config.LastAddress = text.Trim();
        _config.Save();
        ConnectTo(target.Host, target.Port, target.Host, null);
    }

    void ConnectTo(string host, int port, string name, string? phoneId, bool reconnecting = false, bool user = true)
    {
        if (_controller is not null)
            return;
        _autoConnectAllowed = false;
        if (!reconnecting)
            _reconnector.Connecting(new Target(host, port, name, phoneId), user);
        ShowConnecting(Text.Format(reconnecting ? "ReconnectingTo" : "ConnectingTo", name));
        _controller = new Controller(host, port, Config.ComputerName(), onError => new NativeVideoOutput(_camera, onError), this, _config,
            Post, allowPairing: _reconnector.PairingAllowed);
        _controller.Start();
    }

    void Disconnect()
    {
        _reconnector.Cancel();
        if (_reconnectTimer.IsEnabled)
        {
            _reconnectTimer.Stop();
            ShowPhones();
        }
        _controller?.Stop();
    }

    void OnCancel(object sender, RoutedEventArgs e) => Disconnect();

    void OnSwitchCamera(object sender, RoutedEventArgs e)
    {
        if (_controller is null)
            return;
        _facing = _facing == "front" ? "back" : "front";
        _controller.SetCamera(_facing);
    }

    void Reconnect()
    {
        _reconnectTimer.Stop();
        if (_reconnector.Attempting() is { } target)
            ConnectTo(target.Host, target.Port, target.Name, target.PhoneId, reconnecting: true);
    }

    // IControllerEvents, on the window's thread.

    public void OnPending(string code) =>
        ShowConnecting(Text.Get("TapAllow"), Text.Format("PhoneAsking", Config.ComputerName()) + " " + Text.Get("SameCode"), code);

    public void OnCamera(string facing) => _facing = facing;

    public void OnStreaming(StreamInfo info)
    {
        _facing = info.Facing;
        _reconnector.Streaming(info.DeviceId.Length > 0 ? info.DeviceId : null);
        if (info.DeviceId.Length > 0)
        {
            _config.LastPhoneId = info.DeviceId;
            _config.Save();
        }
        StreamingTitle.Text = Text.Format("StreamingFrom", info.DeviceName);
        StreamingHint.Text = _camera is not null
            ? Text.Format("ChooseCamera", VirtualCamera.Name)
            : Text.Get("PreviewOnly");
        ClearPreview();
        CompositionTarget.Rendering += UpdatePreview;
        ShowPage(StreamingPage);
    }

    public void OnEnded(string reason)
    {
        var detail = _controller?.OutputError;
        _controller = null;
        CompositionTarget.Rendering -= UpdatePreview;
        if (_reconnector.Ended(reason) is { } retry)
        {
            var (target, delay) = retry;
            ShowConnecting(Text.Format("ReconnectingTo", target.Name), Text.Get("ConnectionDropped"));
            _browser.Refresh(); // find the phone again in case its address changed
            _reconnectTimer.Interval = TimeSpan.FromSeconds(Math.Max(delay, 0.01));
            _reconnectTimer.Start();
            return;
        }
        ShowPhones();
        RenderPhones();
        if (reason != EndReasons.Closed)
            ShowToast(reason == EndReasons.VideoError && detail is not null ? $"{EndReasons.Message(reason)} {detail}" : EndReasons.Message(reason));
    }

    void ClearPreview()
    {
        _previewSeen = 0;
        _preview.Lock();
        unsafe
        {
            new Span<byte>((void*)_preview.BackBuffer, _preview.BackBufferStride * _preview.PixelHeight).Clear();
        }
        _preview.AddDirtyRect(new Int32Rect(0, 0, _preview.PixelWidth, _preview.PixelHeight));
        _preview.Unlock();
    }

    void UpdatePreview(object? sender, EventArgs e)
    {
        if (_controller?.Output is not NativeVideoOutput output)
            return;
        _preview.Lock();
        var seen = output.CopyPreview(_previewSeen, _preview.BackBuffer, _preview.BackBufferStride);
        if (seen != _previewSeen)
            _preview.AddDirtyRect(new Int32Rect(0, 0, _preview.PixelWidth, _preview.PixelHeight));
        _previewSeen = seen;
        _preview.Unlock();
    }

    // Menu

    void OnMenu(object sender, RoutedEventArgs e)
    {
        var menu = MenuButton.ContextMenu;
        menu.PlacementTarget = MenuButton;
        menu.Placement = System.Windows.Controls.Primitives.PlacementMode.Bottom;
        // Right-aligned under the button, so the menu stays inside the window.
        menu.Opened += AlignMenu;
        menu.IsOpen = true;
    }

    void AlignMenu(object? sender, RoutedEventArgs e)
    {
        var menu = MenuButton.ContextMenu;
        menu.Opened -= AlignMenu;
        menu.HorizontalOffset = MenuButton.ActualWidth - menu.ActualWidth;
    }

    void OnLanguage(object sender, RoutedEventArgs e)
    {
        var dialog = new LanguageDialog(_config.Language) { Owner = this };
        if (dialog.ShowDialog() != true || dialog.Chosen == _config.Language)
            return;
        _config.Language = dialog.Chosen;
        _config.Save();
        if (!((App)Application.Current).Restart())
            ShowToast(Text.Get("RestartFailed"));
    }

    void OnPairedPhones(object sender, RoutedEventArgs e) => new PairedPhonesWindow(_config, Forget) { Owner = this }.ShowDialog();

    /// <summary>Forgets phones; disconnects first if one of them is in use.</summary>
    void Forget(IReadOnlyList<PairedPhone> phones, string message)
    {
        var target = _reconnector.Target;
        var busy = _controller is not null || _reconnector.Reconnecting || _reconnectTimer.IsEnabled;
        if (busy && target is not null && phones.Any(p => p.PhoneId == target.PhoneId))
            Disconnect();
        foreach (var phone in phones)
            _config.Forget(phone.Fingerprint);
        RenderPhones();
        ShowToast(message);
    }

    void OnAbout(object sender, RoutedEventArgs e) =>
        MessageBox.Show(this, $"Beamcam {typeof(App).Assembly.GetName().Version?.ToString(3)}\n\n{Text.Get("Tagline")}", Text.Get("AboutBeamcam"));

    protected override void OnClosing(CancelEventArgs e)
    {
        _closing = true;
        _browser.Stop();
        _reconnectTimer.Stop();
        CompositionTarget.Rendering -= UpdatePreview;
        if (_controller is { } controller)
        {
            controller.Stop();
            controller.Session.Join(TimeSpan.FromSeconds(2));
            controller.Output?.Dispose();
        }
        _camera?.Dispose();
        base.OnClosing(e);
    }
}
