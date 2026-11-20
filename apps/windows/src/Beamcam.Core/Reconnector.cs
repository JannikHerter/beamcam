namespace Beamcam;

public sealed record Target(string Host, int Port, string Name, string? PhoneId = null);

/// <summary>
/// Whether and where to reconnect after a stream drops. Same rules as apps/linux/src/beamcam/reconnect.py.
///
/// Phones often come back from a Wi-Fi drop with a new address (random MAC per connection), so the
/// phone is followed by its device id through mDNS instead of retrying the old address. Pure; the
/// window owns the timers.
/// </summary>
public sealed class Reconnector(int attempts = Reconnector.Attempts)
{
    public const int Attempts = 10;
    public const double Delay = 3.0;

    // A failed connect is retried only while reconnecting; on a first, user-started connect the
    // user should hear about it right away.
    static readonly HashSet<string> Retryable = [EndReasons.ConnectionLost, EndReasons.ConnectFailed];

    int _left;
    Target? _attempted; // where the current or last attempt went
    bool _user; // the current attempt was started by the user, not automatically
    bool _repaired;

    public Target? Target { get; private set; }
    public bool Reconnecting => _left > 0;

    /// <summary>Only a connection the user asked for may pair with a phone; automatic ones never do.</summary>
    public bool PairingAllowed => _user;

    /// <summary>A new connection (clicked, or the automatic one at startup); nothing is retried until
    /// it has streamed once.</summary>
    public void Connecting(Target target, bool user = true)
    {
        Target = target;
        _attempted = target;
        _left = 0;
        _user = user;
        _repaired = false;
    }

    /// <summary>The address for the next retry, recorded as the one being tried.</summary>
    public Target? Attempting()
    {
        _attempted = Target;
        return Target;
    }

    public void Streaming(string? phoneId)
    {
        if (Target is null)
            return;
        if (!string.IsNullOrEmpty(phoneId) && string.IsNullOrEmpty(Target.PhoneId))
            Target = Target with { PhoneId = phoneId };
        _left = attempts;
        _user = false;
    }

    /// <summary>Follow the phone to its current address. True if it moved.</summary>
    public bool Phones(IEnumerable<Phone> phones)
    {
        if (Target is not { PhoneId: { Length: > 0 } id } target)
            return false;
        var phone = phones.FirstOrDefault(p => p.Id == id);
        if (phone is null || (phone.Host == target.Host && phone.Port == target.Port))
            return false;
        Target = target with { Host = phone.Host, Port = phone.Port };
        return true;
    }

    /// <summary>(where, delay in seconds) for the next attempt, or null to stop. No delay when the phone
    /// has moved since the last attempt, since that attempt went to a dead address.</summary>
    public (Target Target, double Delay)? Ended(string reason)
    {
        if (reason == EndReasons.Forgotten && _user && !_repaired && Target is not null)
        {
            // The phone forgot us; the user clicked Connect, so pair again right away (the phone asks).
            _repaired = true;
            return (Target, 0.0);
        }
        if (Target is null || _left <= 0 || !Retryable.Contains(reason))
        {
            Cancel();
            return null;
        }
        _left--;
        var moved = _attempted is not null && (_attempted.Host != Target.Host || _attempted.Port != Target.Port);
        return (Target, moved ? 0.0 : Delay);
    }

    public void Cancel() => _left = 0;
}
