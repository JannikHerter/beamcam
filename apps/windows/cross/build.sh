#!/bin/sh
# Builds the Windows app on Linux, in containers, into apps/windows/out/:
#   out/app/          the app (self-contained .NET, plus BeamcamCamera.dll)
#   out/tools/        beamcam-capture.exe and convert_tests.exe, for testing in Windows
# The installer is made on Windows from out/app (see installer/Beamcam.iss).
set -eu
cd "$(dirname "$0")/.."
root=$(pwd)
uid=$(id -u):$(id -g)

docker build -q -t beamcam-cross cross >/dev/null
docker run --rm -u "$uid" -v "$root":/src -w /src beamcam-cross sh -c '
  cmake -S native -B out/native -G Ninja -DCMAKE_BUILD_TYPE=Release -DCMAKE_TOOLCHAIN_FILE=/src/cross/toolchain.cmake >/dev/null &&
  cmake --build out/native'

docker run --rm -u "$uid" -e HOME=/tmp -e DOTNET_CLI_TELEMETRY_OPTOUT=1 -e DOTNET_NOLOGO=1 \
  -v "$root":/src -v beamcam-nuget:/tmp/.nuget -w /src mcr.microsoft.com/dotnet/sdk:10.0 sh -c '
  dotnet publish src/Beamcam/Beamcam.csproj -c Release -r win-x64 --self-contained -o out/app -v quiet &&
  dotnet publish src/Beamcam.Cli/Beamcam.Cli.csproj -c Release -r win-x64 --self-contained -o out/app -v quiet'

cp out/native/BeamcamCamera.dll out/app/
mkdir -p out/tools
cp out/native/beamcam-capture.exe out/native/convert_tests.exe out/tools/
echo "built $root/out/app"
