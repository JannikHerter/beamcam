#!/bin/sh
# Installs what a distro package adds next to the Python package: menu entry, icon, app info,
# and the virtual camera helper with its polkit action. Every package in packaging/ uses this.
# The .deb and .rpm also get the helper that adds Beamcam's repository and the key it is signed
# with (docs/internals/updates.md); the AUR updates the Arch package itself.
set -eu
dest="${1:?usage: install-data.sh DESTDIR [deb|rpm]}"
root="$(cd "$(dirname "$0")/.." && pwd)"
id=org.beamcam.Beamcam
install -Dm644 "$root/data/$id.desktop" "$dest/usr/share/applications/$id.desktop"
install -Dm644 "$root/data/$id.svg" "$dest/usr/share/icons/hicolor/scalable/apps/$id.svg"
install -Dm644 "$root/data/$id.metainfo.xml" "$dest/usr/share/metainfo/$id.metainfo.xml"
install -Dm644 "$root/data/$id.policy" "$dest/usr/share/polkit-1/actions/$id.policy"
# The path is fixed: the polkit action and virtualcam.INSTALLED_HELPER name it.
install -Dm755 "$root/src/beamcam/data/setup-virtual-camera" "$dest/usr/lib/beamcam/setup-virtual-camera"
case "${2:-}" in
    deb) install -Dm644 "$root/packaging/beamcam.asc" "$dest/usr/share/keyrings/beamcam.asc" ;;
    rpm) install -Dm644 "$root/packaging/beamcam.asc" "$dest/etc/pki/rpm-gpg/RPM-GPG-KEY-beamcam" ;;
    "") exit 0 ;;
    *) echo "unknown package format: $2" >&2; exit 2 ;;
esac
# The path is fixed: the polkit action and updates.HELPER name it.
install -Dm755 "$root/data/updates" "$dest/usr/lib/beamcam/updates"
