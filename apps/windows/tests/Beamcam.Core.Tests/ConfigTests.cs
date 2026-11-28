namespace Beamcam.Tests;

// Mirrors apps/linux/tests/test_config.py.
public sealed class ConfigTests : IDisposable
{
    readonly string _directory = Directory.CreateTempSubdirectory("beamcam-test").FullName;
    string ConfigPath => Path.Combine(_directory, "config.json");

    public void Dispose() => Directory.Delete(_directory, recursive: true);

    static readonly byte[] Key = Enumerable.Repeat((byte)1, 32).ToArray();

    [Fact]
    public void PairingsSurviveARestartAndStayPrivate()
    {
        Config.Load(ConfigPath).Pair("fp1", Key, "phone-1", "Pixel");
        var loaded = Config.Load(ConfigPath);
        Assert.Equal(Key, loaded.SecretFor("fp1"));
        Assert.True(loaded.IsPaired("phone-1"));
        if (!OperatingSystem.IsWindows())
            Assert.Equal(UnixFileMode.UserRead | UnixFileMode.UserWrite, File.GetUnixFileMode(ConfigPath));
    }

    [Fact]
    public void TheUpdateAnswerIsKept()
    {
        Assert.Null(Config.Load(ConfigPath).CheckUpdates);
        var config = Config.Load(ConfigPath);
        config.CheckUpdates = false;
        config.Save();
        Assert.False(Config.Load(ConfigPath).CheckUpdates);
        config.CheckUpdates = true;
        config.Save();
        Assert.True(Config.Load(ConfigPath).CheckUpdates);
    }

    [Fact]
    public void NewestConnectionFirstAndRenamesAreKept()
    {
        var config = Config.Load(ConfigPath);
        config.Pair("fp1", Key, "phone-1", "Pixel");
        config.Pair("fp2", Key, "phone-2", "Galaxy");
        Thread.Sleep(1100);
        config.Connected("fp1", "Work phone");
        Assert.Equal(["Work phone", "Galaxy"], Config.Load(ConfigPath).PairedPhones().Select(p => p.Name));
    }

    [Fact]
    public void ConnectedDoesNotPairAnUnknownPhone()
    {
        var config = Config.Load(ConfigPath);
        config.Connected("fp-unknown", "Stranger");
        Assert.Empty(config.PairedPhones());
    }

    [Fact]
    public void ForgettingOnePhoneKeepsTheOthers()
    {
        var config = Config.Load(ConfigPath);
        config.Pair("fp1", Key, "phone-1", "Pixel");
        config.Pair("fp2", Key, "phone-2", "Galaxy");
        config.LastPhoneId = "phone-1";
        config.Forget("fp1");
        Assert.Equal(["phone-2"], config.PairedPhones().Select(p => p.PhoneId));
        Assert.Null(config.LastPhoneId);
        config.ForgetAll();
        Assert.Empty(Config.Load(ConfigPath).PairedPhones());
    }

    [Fact]
    public void AStrangerClaimingAPhonesIdCannotEraseItsPairing()
    {
        var config = Config.Load(ConfigPath);
        config.Pair("real-key", Key, "phone-1", "Pixel");
        config.LastPhoneId = "phone-1";
        config.Pair("other-key", new byte[32], "phone-1", "Pixel");
        Assert.Equal(Key, config.SecretFor("real-key"));
        config.Forget("other-key");
        Assert.Equal("phone-1", config.LastPhoneId);
        Assert.Equal(Key, Config.Load(ConfigPath).SecretFor("real-key"));
    }

    [Theory]
    [InlineData("""{"pairings": {"fp1": {"secret": "01", "secret": "02"}}}""")]
    [InlineData("""{"last_phone_id": "a", "last_phone_id": "b"}""")]
    public void DuplicateKeysStartEmpty(string text)
    {
        File.WriteAllText(ConfigPath, text);
        Assert.Empty(Config.Load(ConfigPath).PairedPhones());
    }

    [Theory]
    [InlineData("99999999999999")]
    [InlineData("-5")]
    public void ImpossibleDatesAreIgnored(string seconds)
    {
        File.WriteAllText(ConfigPath, """{"pairings": {"fp1": {"secret": "01", "phone_id": "p", "name": "P", "last_connected": """ + seconds + "}}}");
        Assert.Equal(0, Assert.Single(Config.Load(ConfigPath).PairedPhones()).LastConnected);
    }

    [Fact]
    public void ReadsTheLinuxAppsFormat()
    {
        // Same fields as ~/.config/beamcam/config.json, including a pairing from before last_connected was stored.
        File.WriteAllText(ConfigPath, """
            {"last_phone_id": "phone-1", "last_address": "192.168.1.23",
             "pairings": {"fp1": {"secret": "0101010101010101010101010101010101010101010101010101010101010101", "phone_id": "phone-1", "name": "Pixel"}}}
            """);
        var config = Config.Load(ConfigPath);
        Assert.Equal(("phone-1", "192.168.1.23"), (config.LastPhoneId, config.LastAddress));
        var phone = Assert.Single(config.PairedPhones());
        Assert.Equal(("Pixel", 0L), (phone.Name, phone.LastConnected));
        Assert.Equal(Key, config.SecretFor("fp1"));
    }

    [Fact]
    public void UnreadableFileStartsEmpty()
    {
        File.WriteAllText(ConfigPath, "{not json");
        Assert.Empty(Config.Load(ConfigPath).PairedPhones());
    }
}
