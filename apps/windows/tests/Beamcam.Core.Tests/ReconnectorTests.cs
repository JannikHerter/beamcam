namespace Beamcam.Tests;

// Mirrors apps/linux/tests/test_reconnect.py.
public class ReconnectorTests
{
    static readonly Target PhoneTarget = new("192.168.2.147", 47800, "Pixel", "phone-1");

    static Reconnector Streamed(Target? target = null, int attempts = 3)
    {
        var r = new Reconnector(attempts);
        r.Connecting(target ?? PhoneTarget);
        r.Streaming("phone-1");
        return r;
    }

    [Fact]
    public void FirstConnectFailureIsNotRetried()
    {
        var r = new Reconnector();
        r.Connecting(PhoneTarget);
        Assert.Null(r.Ended(EndReasons.ConnectFailed));
    }

    [Fact]
    public void LostStreamRetriesAfterDelay() => Assert.Equal((PhoneTarget, Reconnector.Delay), Streamed().Ended(EndReasons.ConnectionLost));

    [Fact]
    public void FailedReconnectKeepsRetryingUntilAttemptsRunOut()
    {
        var r = Streamed(attempts: 2);
        Assert.NotNull(r.Ended(EndReasons.ConnectionLost));
        r.Attempting();
        Assert.NotNull(r.Ended(EndReasons.ConnectFailed));
        r.Attempting();
        Assert.Null(r.Ended(EndReasons.ConnectFailed));
        Assert.False(r.Reconnecting);
    }

    [Fact]
    public void FollowsPhoneToNewAddress()
    {
        var r = Streamed();
        r.Ended(EndReasons.ConnectionLost);
        r.Attempting(); // goes to the old .147 and fails
        Assert.True(r.Phones([new Phone("phone-1", "Pixel", "192.168.2.148", 47800)]));
        var (target, delay) = r.Ended(EndReasons.ConnectFailed)!.Value;
        Assert.Equal(("192.168.2.148", 0.0), (target.Host, delay));
    }

    [Fact]
    public void ManualConnectLearnsPhoneIdFromAccept()
    {
        var r = new Reconnector();
        r.Connecting(new Target("192.168.2.147", 47800, "192.168.2.147"));
        r.Streaming("phone-1");
        Assert.True(r.Phones([new Phone("phone-1", "Pixel", "192.168.2.150", 47800)]));
        Assert.Equal("192.168.2.150", r.Target!.Host);
    }

    [Fact]
    public void OtherPhonesAndSameAddressDoNotCount()
    {
        var r = Streamed();
        Assert.False(r.Phones([new Phone("other", "X", "192.168.2.9", 47800)]));
        Assert.False(r.Phones([new Phone("phone-1", "Pixel", "192.168.2.147", 47800)]));
    }

    [Theory]
    [InlineData(EndReasons.Closed)]
    [InlineData("stopped")]
    [InlineData("denied")]
    [InlineData("replaced")]
    [InlineData(EndReasons.UnknownPhone)]
    public void UserStopAndPhoneEndReasonsAreFinal(string reason) => Assert.Null(Streamed().Ended(reason));

    [Fact]
    public void OnlyAClickMayPair()
    {
        var r = new Reconnector();
        r.Connecting(PhoneTarget);
        Assert.True(r.PairingAllowed);
        r.Connecting(PhoneTarget, user: false); // the automatic connect at startup
        Assert.False(r.PairingAllowed);
        r = Streamed();
        r.Ended(EndReasons.ConnectionLost);
        Assert.False(r.PairingAllowed); // reconnects never pair
    }

    [Fact]
    public void ClickedConnectToAPhoneThatForgotUsPairsAgainOnce()
    {
        var r = new Reconnector();
        r.Connecting(PhoneTarget);
        Assert.Equal((PhoneTarget, 0.0), r.Ended(EndReasons.Forgotten));
        Assert.True(r.PairingAllowed);
        Assert.Null(r.Ended(EndReasons.Forgotten));
    }

    [Fact]
    public void AutomaticConnectToAPhoneThatForgotUsStops()
    {
        var r = new Reconnector();
        r.Connecting(PhoneTarget, user: false);
        Assert.Null(r.Ended(EndReasons.Forgotten));
        Assert.Null(Streamed().Ended(EndReasons.Forgotten));
    }

    [Fact]
    public void StreamingAgainResetsAttempts()
    {
        var r = Streamed(attempts: 1);
        r.Ended(EndReasons.ConnectionLost);
        r.Streaming("phone-1");
        Assert.NotNull(r.Ended(EndReasons.ConnectionLost));
    }
}
