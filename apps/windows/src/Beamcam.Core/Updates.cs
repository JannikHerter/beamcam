// New versions from the GitHub release, as docs/internals/updates.md describes. Only checked once
// the user said yes (Config.CheckUpdates).
using System.Net.Http.Headers;
using System.Security.Cryptography;
using System.Text.Json;
using System.Text.Json.Nodes;
using System.Text.RegularExpressions;

namespace Beamcam;

public sealed record Update(string Version, string FileName, long Size, string Sha256)
{
    public Uri Url => new($"{Updates.DownloadBase}v{Version}/{FileName}");
}

public static partial class Updates
{
    const string ManifestUrl = "https://github.com/JannikHerter/beamcam/releases/latest/download/update.json";
    internal const string DownloadBase = "https://github.com/JannikHerter/beamcam/releases/download/";
    const string Platform = "windows";
    const int MaxManifest = 64 * 1024;
    const int MaxSignature = 4096;
    const long MaxFile = 1L << 30;
    static readonly byte[] SignedPrefix = "beamcam-update-v1\n"u8.ToArray();

    // The public half of the release key, whose certificate release.yml pins.
    const string ReleaseKey =
        "MIICIjANBgkqhkiG9w0BAQEFAAOCAg8AMIICCgKCAgEAxhDZoAUvIMPK2PUckR3rvT4bXRhwLEWUknEhsWrQmhdg5NgpbMZiqpMwumrVnKkeKZmm4gVvDJ6cZs6ftP1y8a8PDuPcapQiVD0b9poLcw2svnOEz/xJ44173lEsW1TNt46Vsg8CU+Y6a4bRC0j25Rr/GWm2+Tt03mVbqHjSSprCmfp/BPCQlwD9n7XoS9ug2N5LE7/6kJxOGzjjMssJJ+WM6KWLR7jSPBdesnyV19c1WgdxAKdoMJiFdY14Mlh5pLxtcPP308ii586LupLVMwW5Rit5q9nkebzh9VQDIS8pQKHsHPfm7h9beEvUKkqel6UyceNIJq1QerZteo8auvi9Hvo/ag34sWpkSKlZhR5SE2SLCEJ+gfRBMgwi28Rdb2ORHyN8lrv/I4JDEEH3sfbjWcHCmL7WktsIzwGxjdKSQ1Ea7AheekaRkihGTD9MwzHJPjgbpIIcng7NYAVEoCR1paLcrQekKrtitg27V4ynwUwQ/Puu2J91iG5/XtyME8QeXo1noXwC7n2l6MePYKTCDmMBXC+ltNMNKuilHlnnNaqTrvC2MJvHfTJn4EUy6VCgxxmbJVENxKGm3+g6Fuhh1mIE3sFmOcW/KlmYC0aSlLi7jkhhzaYYX6H+8qMqyCWigHiQfbohFHDY9LSQs0K63iYocwh0zfbVP4grznkCAwEAAQ==";

    public static string CurrentVersion => typeof(Updates).Assembly.GetName().Version?.ToString(3) ?? "0.0.0";

    public static HttpClient Client()
    {
        var http = new HttpClient { Timeout = TimeSpan.FromMinutes(1) };
        http.DefaultRequestHeaders.UserAgent.Add(new ProductInfoHeaderValue("Beamcam", CurrentVersion));
        return http;
    }

    /// <summary>The newer version for Windows, or null if there is none (or no release yet).</summary>
    public static async Task<Update?> CheckAsync(HttpClient http, CancellationToken cancel = default)
    {
        using var timeout = CancellationTokenSource.CreateLinkedTokenSource(cancel);
        timeout.CancelAfter(TimeSpan.FromSeconds(30));
        var manifest = await GetSmall(http, ManifestUrl, MaxManifest, timeout.Token);
        var signature = await GetSmall(http, ManifestUrl + ".sig", MaxSignature, timeout.Token);
        if (manifest is null || signature is null)
            return null;
        using var key = RSA.Create();
        key.ImportSubjectPublicKeyInfo(Convert.FromBase64String(ReleaseKey), out _);
        return Read(manifest, signature, CurrentVersion, key);
    }

    static async Task<byte[]?> GetSmall(HttpClient http, string url, int limit, CancellationToken cancel)
    {
        using var response = await http.GetAsync(url, HttpCompletionOption.ResponseHeadersRead, cancel);
        if (response.StatusCode == System.Net.HttpStatusCode.NotFound)
            return null;
        response.EnsureSuccessStatusCode();
        await using var stream = await response.Content.ReadAsStreamAsync(cancel);
        var buffer = new MemoryStream();
        var chunk = new byte[8192];
        int read;
        while ((read = await stream.ReadAsync(chunk, cancel)) > 0)
        {
            if (buffer.Length + read > limit)
                throw new InvalidDataException($"{url} is larger than {limit} bytes");
            buffer.Write(chunk, 0, read);
        }
        return buffer.ToArray();
    }

    /// <summary>The update in a signed update.json, if it is newer than <paramref name="current"/>.</summary>
    internal static Update? Read(byte[] manifest, byte[] signature, string current, RSA key)
    {
        if (!key.VerifyData((byte[])[.. SignedPrefix, .. manifest], signature, HashAlgorithmName.SHA256, RSASignaturePadding.Pkcs1))
        {
            Log.Warning("update.json: the signature does not match the release key");
            return null;
        }
        try
        {
            if (JsonNode.Parse(manifest) is not JsonObject data || data["files"]?[Platform] is not JsonObject file)
                return null;
            var version = data["version"]?.GetValue<string>();
            var name = file["name"]?.GetValue<string>();
            var size = file["size"]?.GetValue<long>() ?? 0;
            var sha256 = file["sha256"]?.GetValue<string>();
            if (version is null || !IsNewer(version, current) || name is null || !FileNamePattern().IsMatch(name)
                || size is <= 0 or > MaxFile || sha256 is null || !Sha256Pattern().IsMatch(sha256))
                return null;
            return new Update(version, name, size, sha256);
        }
        catch (Exception error) when (error is JsonException or InvalidOperationException or FormatException)
        {
            Log.Warning($"update.json: {error.Message}");
            return null;
        }
    }

    /// <summary>True when both are X.Y.Z and <paramref name="candidate"/> is higher.</summary>
    internal static bool IsNewer(string candidate, string current)
    {
        if (!VersionPattern().IsMatch(candidate) || !VersionPattern().IsMatch(current))
            return false;
        var a = candidate.Split('.').Select(int.Parse).ToArray();
        var b = current.Split('.').Select(int.Parse).ToArray();
        for (var i = 0; i < 3; i++)
            if (a[i] != b[i])
                return a[i] > b[i];
        return false;
    }

    /// <summary>Downloads the installer into <paramref name="folder"/> and checks it against update.json.
    /// Throws if it does not match; the file is gone then.</summary>
    public static async Task<string> DownloadAsync(HttpClient http, Update update, string folder, CancellationToken cancel = default)
    {
        if (Directory.Exists(folder))
            Directory.Delete(folder, recursive: true);
        Directory.CreateDirectory(folder);
        var path = Path.Combine(folder, update.FileName);
        // HttpClient.Timeout ends with the headers; this bounds the whole download.
        using var timeout = CancellationTokenSource.CreateLinkedTokenSource(cancel);
        timeout.CancelAfter(TimeSpan.FromMinutes(15));
        cancel = timeout.Token;
        try
        {
            using var response = await http.GetAsync(update.Url, HttpCompletionOption.ResponseHeadersRead, cancel);
            response.EnsureSuccessStatusCode();
            await using (var source = await response.Content.ReadAsStreamAsync(cancel))
            await using (var target = new FileStream(path, FileMode.CreateNew, FileAccess.Write))
            {
                using var hash = IncrementalHash.CreateHash(HashAlgorithmName.SHA256);
                var chunk = new byte[81920];
                long total = 0;
                int read;
                while ((read = await source.ReadAsync(chunk, cancel)) > 0)
                {
                    total += read;
                    if (total > update.Size)
                        throw new InvalidDataException("the download is larger than update.json says");
                    hash.AppendData(chunk, 0, read);
                    await target.WriteAsync(chunk.AsMemory(0, read), cancel);
                }
                if (total != update.Size || Convert.ToHexStringLower(hash.GetHashAndReset()) != update.Sha256)
                    throw new InvalidDataException("the download does not match update.json");
            }
            return path;
        }
        catch
        {
            File.Delete(path);
            throw;
        }
    }

    [GeneratedRegex(@"^[0-9]{1,6}\.[0-9]{1,6}\.[0-9]{1,6}\z")]
    private static partial Regex VersionPattern();

    [GeneratedRegex(@"^[A-Za-z0-9_][A-Za-z0-9._-]{0,127}\z")]
    private static partial Regex FileNamePattern();

    [GeneratedRegex(@"^[0-9a-f]{64}\z")]
    private static partial Regex Sha256Pattern();
}
