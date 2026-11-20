#!/bin/sh
# Installs what a distro package adds next to the Python package: menu entry, icon, app info,
# and the virtual camera helper with its polkit action. Every package in packaging/ uses this.
set -eu
dest="${1:?usage: install-data.sh DESTDIR}"
root="$(cd "$(dirname "$0")/.." && pwd)"
id=org.beamcam.Beamcam
install -Dm644 "$root/data/$id.desktop" "$dest/usr/share/applications/$id.desktop"
install -Dm644 "$root/data/$id.svg" "$dest/usr/share/icons/hicolor/scalable/apps/$id.svg"
install -Dm644 "$root/data/$id.metainfo.xml" "$dest/usr/share/metainfo/$id.metainfo.xml"
install -Dm644 "$root/data/$id.policy" "$dest/usr/share/polkit-1/actions/$id.policy"
# The path is fixed: the polkit action and virtualcam.INSTALLED_HELPER name it.
install -Dm755 "$root/src/beamcam/data/setup-virtual-camera" "$dest/usr/lib/beamcam/setup-virtual-camera"
