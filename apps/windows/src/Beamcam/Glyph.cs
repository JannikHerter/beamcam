using System.Windows.Automation.Peers;
using System.Windows.Controls;

namespace Beamcam.App;

/// <summary>An icon drawn with the Segoe Fluent Icons font. Hidden from screen readers, which would
/// otherwise read the private-use character as "?"; the control next to it carries the meaning.</summary>
public sealed class Glyph : TextBlock
{
    protected override AutomationPeer? OnCreateAutomationPeer() => null;
}
