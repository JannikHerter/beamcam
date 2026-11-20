#!/usr/bin/env bash
# End-to-end check: Android app on a running emulator -> Linux app -> (optionally) the virtual camera.
# See docs/operations/development.md#end-to-end-test.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT="${E2E_OUT:-$(mktemp -d)}"
BUILD=1
LAUNCH=0
for arg in "$@"; do
  case "$arg" in
    --no-build) BUILD=0 ;;
    --launch) LAUNCH=1 ;;
    *) echo "usage: $0 [--no-build] [--launch]" >&2; exit 2 ;;
  esac
done

step() { printf '\n== %s\n' "$*"; }
fail() { echo "FAIL: $*" >&2; exit 1; }

adb get-state >/dev/null 2>&1 || fail "no emulator or device connected (adb devices)"

if [ "$BUILD" = 1 ]; then
  step "Building the Android app"
  (cd "$ROOT/apps/android" && ./gradlew -q assembleDebug)
fi

step "Installing (camera and notification permissions pre-granted)"
adb install -r -g "$ROOT/apps/android/app/build/outputs/apk/debug/app-debug.apk" >/dev/null
if [ "$LAUNCH" = 1 ]; then
  adb shell am start -W -n org.beamcam/.MainActivity >/dev/null
else
  echo "Open the Beamcam app on the emulator now."
fi

# The emulator is behind NAT; reach its server through adb.
adb forward tcp:47800 tcp:47800 >/dev/null

step "Waiting for the phone to listen"
for _ in $(seq 60); do
  adb shell 'cat /proc/net/tcp /proc/net/tcp6' 2>/dev/null | grep -qi ":BAB8 .* 0A " && break
  sleep 1
done
adb shell 'cat /proc/net/tcp /proc/net/tcp6' | grep -qi ":BAB8 .* 0A " || fail "the app is not listening on port 47800"

export PYTHONPATH="$ROOT/apps/linux/src"
# Pairings with emulators must not end up in the developer's real config.
export XDG_CONFIG_HOME="${E2E_CONFIG:-$OUT/config}"
cd "$OUT"

step "Decoding 150 frames (tap Allow on the emulator if it asks)"
python3 -m beamcam stream 127.0.0.1:47800 --no-camera --frames 150 --snapshot "$OUT/decoded.png" \
  || fail "streaming did not work"

step "Switching to the back camera"
python3 -m beamcam stream 127.0.0.1:47800 --no-camera --frames 90 --camera back --snapshot "$OUT/back.png" \
  || fail "camera switch did not work"
python3 -m beamcam stream 127.0.0.1:47800 --no-camera --frames 30 --camera front >/dev/null

DEVICE="$(python3 -c 'from beamcam import virtualcam as v; c = v.find_camera(); print(c.path if c and c.writable else "")')"
if [ -z "$DEVICE" ]; then
  step "Skipping the virtual camera check (no writable v4l2loopback device)"
else
  step "Writing to $DEVICE and reading it back like a video app"
  python3 -m beamcam stream 127.0.0.1:47800 --device "$DEVICE" --frames 450 > "$OUT/producer.log" 2>&1 &
  producer=$!
  for _ in $(seq 40); do grep -q "Streaming from" "$OUT/producer.log" && break; sleep 0.5; done
  grep -q "Streaming from" "$OUT/producer.log" || { cat "$OUT/producer.log"; fail "producer did not start"; }
  sleep 1
  timeout 20 gst-launch-1.0 -q v4l2src device="$DEVICE" num-buffers=30 ! videoconvert ! pngenc snapshot=true \
    ! filesink location="$OUT/from-virtual-camera.png" || fail "could not read from $DEVICE"
  timeout 20 ffmpeg -hide_banner -loglevel error -f v4l2 -i "$DEVICE" -frames:v 60 -f null - \
    || fail "ffmpeg could not read from $DEVICE"
  wait "$producer" || fail "producer exited with an error"
  echo "Virtual camera works: $DEVICE"
fi

step "PASS"
echo "Frames saved in $OUT"
