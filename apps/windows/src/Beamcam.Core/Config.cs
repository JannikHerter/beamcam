// Persistent settings in %LOCALAPPDATA%\Beamcam\config.json, the same fields as the Linux app's
// ~/.config/beamcam/config.json. Local, not roaming: a phone allowed this computer, not every
// computer the user signs in to.
using System.Net;
using System.Text.Json;
using System.Text.Json.Nodes;

namespace Beamcam;

public sealed record PairedPhone(string Fingerprint, string PhoneId, string Name, long LastConnected);

public sealed class Config : IPairingStore
{
    static readonly Lock Guard = new();

    /// <summary>Phones this computer paired with, by certificate fingerprint (hex). The secret is what
    /// the phone allowed.</summary>
    sealed class Pairing
    {
        public string Secret = "";
        public string PhoneId = "";
        public string Name = "Phone";
        public long LastConnected; // unix seconds, 0 if unknown
    }

    readonly Dictionary<string, Pairing> _pairings = [];
    readonly string _path;

    public string? LastPhoneId { get; set; }
    public string? LastAddress { get; set; }

    Config(string path) => _path = path;

    /// <summary>BEAMCAM_CONFIG_DIR overrides the folder, for tests.</summary>
    public static string DefaultPath()
    {
        var directory = Environment.GetEnvironmentVariable("BEAMCAM_CONFIG_DIR");
        if (string.IsNullOrEmpty(directory))
            directory = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "Beamcam");
        return Path.Combine(directory, "config.json");
    }

    public static Config Load(string? path = null)
    {
        var config = new Config(path ?? DefaultPath());
        try
        {
            if (JsonNode.Parse(File.ReadAllText(config._path)) is not JsonObject data)
                return config;
            config.LastPhoneId = Text(data["last_phone_id"]);
            config.LastAddress = Text(data["last_address"]);
            if (data["pairings"] is JsonObject pairings)
            {
                foreach (var (fingerprint, node) in pairings)
                {
                    if (node is not JsonObject entry || Text(entry["secret"]) is not { } secret)
                        continue;
                    config._pairings[fingerprint] = new Pairing
                    {
                        Secret = secret,
                        PhoneId = Text(entry["phone_id"]) ?? "",
                        Name = Text(entry["name"]) is { Length: > 0 } name ? name : "Phone",
                        LastConnected = entry["last_connected"] is JsonValue v && v.TryGetValue<long>(out var t) ? t : 0,
                    };
                }
            }
        }
        catch (Exception error) when (error is FileNotFoundException or DirectoryNotFoundException)
        {
        }
        catch (Exception error) when (error is IOException or JsonException or UnauthorizedAccessException or InvalidOperationException)
        {
            Log.Warning($"ignoring unreadable {config._path}: {error.Message}");
        }
        return config;
    }

    static string? Text(JsonNode? node) => node is JsonValue value && value.TryGetValue<string>(out var text) ? text : null;

    public void Save()
    {
        lock (Guard)
        {
            var pairings = new JsonObject();
            foreach (var (fingerprint, p) in _pairings)
                pairings[fingerprint] = new JsonObject
                {
                    ["secret"] = p.Secret, ["phone_id"] = p.PhoneId, ["name"] = p.Name, ["last_connected"] = p.LastConnected,
                };
            var data = new JsonObject { ["last_phone_id"] = LastPhoneId, ["last_address"] = LastAddress, ["pairings"] = pairings };
            try
            {
                Directory.CreateDirectory(Path.GetDirectoryName(_path)!);
                var temporary = _path + ".tmp";
                // Holds pairing secrets. On Windows the folder in the user's profile is private to them;
                // on other systems (tests) make the file so.
                var options = new FileStreamOptions { Mode = FileMode.Create, Access = FileAccess.Write };
                if (!OperatingSystem.IsWindows())
                    options.UnixCreateMode = UnixFileMode.UserRead | UnixFileMode.UserWrite;
                using (var stream = new FileStream(temporary, options))
                using (var writer = new StreamWriter(stream))
                    writer.Write(data.ToJsonString(new JsonSerializerOptions { WriteIndented = true }));
                File.Move(temporary, _path, overwrite: true);
            }
            catch (Exception error) when (error is IOException or UnauthorizedAccessException)
            {
                Log.Warning($"could not save {_path}: {error.Message}");
            }
        }
    }

    // Pairings. Called from session threads and the window, hence the lock.

    public byte[]? SecretFor(string fingerprint)
    {
        lock (Guard)
        {
            if (!_pairings.TryGetValue(fingerprint, out var entry))
                return null;
            try
            {
                return Convert.FromHexString(entry.Secret);
            }
            catch (FormatException)
            {
                return null;
            }
        }
    }

    public void Pair(string fingerprint, byte[] secret, string phoneId, string name)
    {
        lock (Guard)
        {
            // A phone that got a new key (app reinstalled) replaces its old pairing.
            foreach (var old in _pairings.Where(p => phoneId.Length > 0 && p.Value.PhoneId == phoneId).Select(p => p.Key).ToList())
                _pairings.Remove(old);
            _pairings[fingerprint] = new Pairing
            {
                Secret = Convert.ToHexStringLower(secret), PhoneId = phoneId, Name = name, LastConnected = DateTimeOffset.UtcNow.ToUnixTimeSeconds(),
            };
        }
        Save();
    }

    /// <summary>A paired phone accepted again; it may have been renamed.</summary>
    public void Connected(string fingerprint, string name)
    {
        lock (Guard)
        {
            if (!_pairings.TryGetValue(fingerprint, out var entry))
                return;
            entry.Name = name;
            entry.LastConnected = DateTimeOffset.UtcNow.ToUnixTimeSeconds();
        }
        Save();
    }

    /// <summary>Most recently connected first.</summary>
    public List<PairedPhone> PairedPhones()
    {
        lock (Guard)
            return _pairings.Select(p => new PairedPhone(p.Key, p.Value.PhoneId, p.Value.Name, p.Value.LastConnected))
                .OrderByDescending(p => p.LastConnected).ToList();
    }

    public void Forget(string fingerprint)
    {
        lock (Guard)
        {
            if (_pairings.Remove(fingerprint, out var entry) && entry.PhoneId == LastPhoneId)
                LastPhoneId = null;
        }
        Save();
    }

    public void ForgetAll()
    {
        lock (Guard)
        {
            _pairings.Clear();
            LastPhoneId = null;
        }
        Save();
    }

    public bool IsPaired(string phoneId)
    {
        lock (Guard)
            return _pairings.Values.Any(p => p.PhoneId == phoneId);
    }

    /// <summary>The name shown on the phone when this computer asks to connect: the device name from
    /// Windows Settings.</summary>
    public static string ComputerName()
    {
        try
        {
            var name = Dns.GetHostName();
            if (name.Length > 0)
                return name;
        }
        catch (System.Net.Sockets.SocketException)
        {
        }
        return Environment.MachineName is { Length: > 0 } machine ? machine : "Windows computer";
    }
}
