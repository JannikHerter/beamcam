using System.Net;
using System.Security.Cryptography;
using System.Text;

namespace Beamcam.Tests;

public sealed class UpdatesTests : IDisposable
{
    // Made by scripts/sign-update.sh 1.2.3 with a throwaway test key, so the script and the app agree.
    const string ScriptManifest = "ewogICJ2ZXJzaW9uIjogIjEuMi4zIiwKICAiZmlsZXMiOiB7CiAgICAiYW5kcm9pZCI6IHsKICAgICAgIm5hbWUiOiAiQmVhbWNhbS0xLjIuMy5hcGsiLAogICAgICAic2l6ZSI6IDUsCiAgICAgICJzaGEyNTYiOiAiNDU1NjlkYTU3ZjRiN2JmNDcyZDdhODY0ZWY0NzgxNDUxY2FlNjM4M2ZlZTlmYjBhZTQwYzU5YWExY2U0NzViNyIKICAgIH0sCiAgICAid2luZG93cyI6IHsKICAgICAgIm5hbWUiOiAiQmVhbWNhbS1TZXR1cC0xLjIuMy5leGUiLAogICAgICAic2l6ZSI6IDE3LAogICAgICAic2hhMjU2IjogImJhNDRhYjExZDcyZTVmMjUyOTQ2MTY4ZDkzMTQwNGU0OTY1MjE5MGVlYjcwMmFiOTdlNjViMjc1YWJkNzhhYjMiCiAgICB9CiAgfQp9Cg==";
    const string ScriptSignature = "kJajP/IARXk9SX7TPcdJwfp0386toGVQA9eSL7GgGuDlaSTkbJOFzXKqKyRhw6P7VJ0IlZ+FLCAlMDkB5FBO05X8O29BzjyL8iUJ956HznGTx46KBcfXwwOC/oijp0x60icc47o/c6xTnXMfscTFn6mAI/M2I36bp1FNcKQfF0G1bIH6OZeQBsmOJDvVQn7LGsfKCSkHbgWYGXVthdTQVt7yd/kBcpypw5kL8dc+ySwUlM4MP5jm/4wRVx7/WBY/WZZpo+5BnGD2WdDtdLIV6SqbaRHn5elnsyu89ndHL82G//zbsuQ3LghKaqjX1jBt8IPW+bYdWmdsZK+lD4Fn4g==";
    const string ScriptKey = "MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAwr182JGttRralMAGoZtUjEcs3Bw+jOUy4khIRG36eH2JIDPGcuYY+BTsuzRnjSHYINhe43ZvSZArV4fWAOZ0FObm5MCJn8uHkuMb4WxkOm3txADNdxUF8u5VGya5gyAbFFpr21ScGxWiTag83OnFi1PIT+dhw7WlsH5wW8lCdXbov3mYtgb7AhAZavR9/IqvkeZxd78Auu4yakzss7ZGWfD0l0G7/5UDlbSMgqKHowiBsQYnu/2U8yPvXUYYiOhKW5XqGfC2E1newW3jF5TMpDeyLD66fPDdZP/GOBQXc1wqxmDr0lsyPC85uRDdLhHankhi94LMaLAyXMhF6CqJzwIDAQAB";

    readonly RSA _key = RSA.Create(2048);
    readonly string _directory = Directory.CreateTempSubdirectory("beamcam-update").FullName;

    public void Dispose()
    {
        _key.Dispose();
        Directory.Delete(_directory, recursive: true);
    }

    byte[] Sign(byte[] manifest, string prefix = "beamcam-update-v1\n") =>
        _key.SignData((byte[])[.. Encoding.UTF8.GetBytes(prefix), .. manifest], HashAlgorithmName.SHA256, RSASignaturePadding.Pkcs1);

    static byte[] Manifest(string version = "1.2.3", string name = "Beamcam-Setup-1.2.3.exe", string size = "17",
        string sha256 = "ba44ab11d72e5f252946168d931404e49652190eeb702ab97e65b275abd78ab3") =>
        Encoding.UTF8.GetBytes($$"""{"version": "{{version}}", "files": {"windows": {"name": "{{name}}", "size": {{size}}, "sha256": "{{sha256}}"} } }""");

    Update? Read(byte[] manifest, string current = "1.0.0") => Updates.Read(manifest, Sign(manifest), current, _key);

    [Fact]
    public void ReadsWhatTheReleaseScriptSigned()
    {
        using var key = RSA.Create();
        key.ImportSubjectPublicKeyInfo(Convert.FromBase64String(ScriptKey), out _);
        var update = Updates.Read(Convert.FromBase64String(ScriptManifest), Convert.FromBase64String(ScriptSignature), "1.2.2", key);
        Assert.Equal(new Update("1.2.3", "Beamcam-Setup-1.2.3.exe", 17, "ba44ab11d72e5f252946168d931404e49652190eeb702ab97e65b275abd78ab3"), update);
        Assert.Equal("https://github.com/JannikHerter/beamcam/releases/download/v1.2.3/Beamcam-Setup-1.2.3.exe", update!.Url.ToString());
    }

    [Fact]
    public void DropsAnythingNotSignedByTheKey()
    {
        var manifest = Manifest();
        var signature = Sign(manifest);
        Assert.NotNull(Updates.Read(manifest, signature, "1.0.0", _key));
        Assert.Null(Updates.Read(Manifest(version: "1.2.4"), signature, "1.0.0", _key));
        Assert.Null(Updates.Read(manifest, Sign(manifest, prefix: ""), "1.0.0", _key));
        using var other = RSA.Create(2048);
        Assert.Null(Updates.Read(manifest, signature, "1.0.0", other));
    }

    [Fact]
    public void OnlyOffersNewerVersions()
    {
        Assert.NotNull(Read(Manifest(), current: "1.2.2"));
        Assert.Null(Read(Manifest(), current: "1.2.3"));
        Assert.Null(Read(Manifest(), current: "1.10.0"));
        Assert.Null(Read(Manifest(version: "1.3.0-rc.1")));
    }

    [Theory]
    [InlineData("1.0.10", "1.0.9", true)]
    [InlineData("2.0.0", "1.99.99", true)]
    [InlineData("1.0.0", "1.0.0", false)]
    [InlineData("0.9.0", "1.0.0", false)]
    [InlineData("1.0", "0.9.0", false)]
    [InlineData("1.0.0\n", "0.9.0", false)]
    [InlineData("1.0.0", "garbage", false)]
    public void ComparesVersionsByNumber(string candidate, string current, bool newer) =>
        Assert.Equal(newer, Updates.IsNewer(candidate, current));

    [Theory]
    [InlineData("../Beamcam.exe")]
    [InlineData("a/b.exe")]
    [InlineData(".exe")]
    [InlineData("")]
    public void RefusesFileNamesThatLeaveTheRelease(string name) => Assert.Null(Read(Manifest(name: name)));

    [Theory]
    [InlineData("0", "ba44ab11d72e5f252946168d931404e49652190eeb702ab97e65b275abd78ab3")]
    [InlineData("\"17\"", "ba44ab11d72e5f252946168d931404e49652190eeb702ab97e65b275abd78ab3")]
    [InlineData("17", "BA44AB11D72E5F252946168D931404E49652190EEB702AB97E65B275ABD78AB3")]
    [InlineData("17", "1234")]
    public void RefusesBrokenFileEntries(string size, string sha256) => Assert.Null(Read(Manifest(size: size, sha256: sha256)));

    [Fact]
    public void IgnoresOtherPlatformsAndBrokenJson()
    {
        Assert.Null(Read("""{"version": "1.2.3", "files": {"android": {"name": "Beamcam-1.2.3.apk", "size": 5, "sha256": "45569da57f4b7bf472d7a864ef4781451cae6383fee9fb0ae40c59aa1ce475b7"}}}"""u8.ToArray()));
        Assert.Null(Read("not json"u8.ToArray()));
        Assert.Null(Read("[]"u8.ToArray()));
    }

    sealed class Serve(byte[] body) : HttpMessageHandler
    {
        public Uri? Asked;

        protected override Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken cancel)
        {
            Asked = request.RequestUri;
            return Task.FromResult(new HttpResponseMessage(HttpStatusCode.OK) { Content = new ByteArrayContent(body) });
        }
    }

    static readonly Update Installer = new("1.2.3", "Beamcam-Setup-1.2.3.exe", 17, "ba44ab11d72e5f252946168d931404e49652190eeb702ab97e65b275abd78ab3");

    [Fact]
    public async Task DownloadsTheInstallerIntoAFreshFolder()
    {
        var folder = Path.Combine(_directory, "updates");
        Directory.CreateDirectory(folder);
        File.WriteAllText(Path.Combine(folder, "Beamcam-Setup-1.2.2.exe"), "old");
        var serve = new Serve("windows installer"u8.ToArray());
        var path = await Updates.DownloadAsync(new HttpClient(serve), Installer, folder);
        Assert.Equal(Installer.Url, serve.Asked);
        Assert.Equal("windows installer", File.ReadAllText(path));
        Assert.Equal([path], Directory.GetFiles(folder));
    }

    [Theory]
    [InlineData("windows installeR")]
    [InlineData("windows installer and more")]
    [InlineData("windows")]
    public async Task DeletesADownloadThatDoesNotMatch(string body)
    {
        var folder = Path.Combine(_directory, "updates");
        await Assert.ThrowsAsync<InvalidDataException>(() => Updates.DownloadAsync(new HttpClient(new Serve(Encoding.UTF8.GetBytes(body))), Installer, folder));
        Assert.Empty(Directory.GetFiles(folder));
    }
}
