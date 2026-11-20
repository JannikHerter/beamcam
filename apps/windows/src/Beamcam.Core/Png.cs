using System.Buffers.Binary;
using System.IO.Compression;

namespace Beamcam;

/// <summary>Writes BGRA pixels as a PNG, for snapshots. Small on purpose: no image library needed.</summary>
public static class Png
{
    static readonly uint[] CrcTable = Enumerable.Range(0, 256).Select(n =>
    {
        var c = (uint)n;
        for (var k = 0; k < 8; k++)
            c = (c & 1) != 0 ? 0xEDB88320 ^ (c >> 1) : c >> 1;
        return c;
    }).ToArray();

    static uint Crc(ReadOnlySpan<byte> data)
    {
        var c = 0xFFFFFFFFu;
        foreach (var b in data)
            c = CrcTable[(c ^ b) & 0xFF] ^ (c >> 8);
        return c ^ 0xFFFFFFFF;
    }

    static void Chunk(Stream output, string type, ReadOnlySpan<byte> data)
    {
        Span<byte> number = stackalloc byte[4];
        BinaryPrimitives.WriteUInt32BigEndian(number, (uint)data.Length);
        output.Write(number);
        var body = new byte[4 + data.Length];
        System.Text.Encoding.ASCII.GetBytes(type, body);
        data.CopyTo(body.AsSpan(4));
        output.Write(body);
        BinaryPrimitives.WriteUInt32BigEndian(number, Crc(body));
        output.Write(number);
    }

    public static byte[] Encode(int width, int height, ReadOnlySpan<byte> bgra, int stride)
    {
        using var output = new MemoryStream();
        output.Write([0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A]);
        var header = new byte[13];
        BinaryPrimitives.WriteUInt32BigEndian(header, (uint)width);
        BinaryPrimitives.WriteUInt32BigEndian(header.AsSpan(4), (uint)height);
        header[8] = 8; // bits per channel
        header[9] = 2; // RGB
        Chunk(output, "IHDR", header);
        using (var pixels = new MemoryStream())
        {
            using (var zlib = new ZLibStream(pixels, CompressionLevel.Fastest, leaveOpen: true))
            {
                var row = new byte[1 + width * 3];
                for (var y = 0; y < height; y++)
                {
                    var source = bgra.Slice(y * stride, width * 4);
                    for (var x = 0; x < width; x++)
                    {
                        row[1 + x * 3] = source[x * 4 + 2];
                        row[2 + x * 3] = source[x * 4 + 1];
                        row[3 + x * 3] = source[x * 4];
                    }
                    zlib.Write(row);
                }
            }
            Chunk(output, "IDAT", pixels.ToArray());
        }
        Chunk(output, "IEND", []);
        return output.ToArray();
    }
}
