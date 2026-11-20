using System.Globalization;
using System.Windows;
using System.Windows.Media;
using System.Windows.Media.Imaging;

namespace Beamcam.App;

/// <summary>What other apps see from the "Beamcam" camera while no phone streams.</summary>
static class IdleFrame
{
    public static byte[] Render()
    {
        const int width = VirtualCamera.Width, height = VirtualCamera.Height;
        var visual = new DrawingVisual();
        using (var context = visual.RenderOpen())
        {
            context.DrawRectangle(new SolidColorBrush(Color.FromRgb(0x1c, 0x1f, 0x22)), null, new Rect(0, 0, width, height));
            var icon = new BitmapImage(new Uri("pack://application:,,,/Assets/beamcam-256.png"));
            context.DrawImage(icon, new Rect((width - 160) / 2.0, 190, 160, 160));
            var typeface = new Typeface(new FontFamily("Segoe UI Variable Display, Segoe UI"), FontStyles.Normal, FontWeights.SemiBold, FontStretches.Normal);
            void Text(string text, double size, Color color, double y)
            {
                var formatted = new FormattedText(text, CultureInfo.CurrentUICulture, FlowDirection.LeftToRight, typeface, size, new SolidColorBrush(color), 1.0);
                context.DrawText(formatted, new Point((width - formatted.Width) / 2, y));
            }
            Text(Beamcam.Text.Get("WaitingForPhone"), 44, Colors.White, 390);
            Text(Beamcam.Text.Get("ConnectInApp"), 26, Color.FromRgb(0xb8, 0xbe, 0xc4), 456);
        }
        var bitmap = new RenderTargetBitmap(width, height, 96, 96, PixelFormats.Pbgra32);
        bitmap.Render(visual);
        var pixels = new byte[width * height * 4];
        bitmap.CopyPixels(pixels, width * 4, 0);
        return pixels;
    }
}
