#!/bin/sh
# Installs a built package in a clean container, without recommended packages, and checks that the
# app starts and finds every GStreamer element it needs: packaging/check-install.sh PACKAGE [image]
set -eu
dir="$(cd "$(dirname "$1")" && pwd)"
name="$(basename "$1")"
case "$name" in
    *.deb) image="${2:-debian:13}"
        install="apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -qq -y --no-install-recommends" ;;
    *.rpm) image="${2:-fedora:45}"
        install="dnf -q -y install --setopt=install_weak_deps=False" ;;
    *.pkg.tar.*) image="${2:-archlinux:latest}"
        install="pacman -Syu --noconfirm >/dev/null && pacman -U --noconfirm" ;;
    *) echo "not a package: $1" >&2; exit 2 ;;
esac
docker run --rm -i -v "$dir:/pkg:ro" "$image" sh -eu <<EOF
$install "/pkg/$name" >/dev/null
beamcam --version
test -x /usr/lib/beamcam/setup-virtual-camera
python3 - <<'PY'
import sys
sys.path.insert(0, "/usr/share/beamcam")  # where the Fedora package keeps the code
from beamcam.pipeline import missing_elements
missing = missing_elements(preview=True)
sys.exit(f"missing GStreamer elements: {missing}" if missing else None)
PY
echo "$name installs and finds everything it needs on \$(uname -m)"
EOF
