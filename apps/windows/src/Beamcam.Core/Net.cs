using System.Net.Sockets;

namespace Beamcam;

static class Net
{
    // Windows reports an ICMP "port unreachable" for an earlier datagram as an error on the next
    // receive (WSAECONNRESET), which would end the receive loop over a single lost heartbeat.
    const int SioUdpConnReset = unchecked((int)0x9800000C);

    public static void IgnoreConnectionResets(Socket socket)
    {
        if (OperatingSystem.IsWindows())
            socket.IOControl(SioUdpConnReset, [0, 0, 0, 0], null);
    }
}
