#!/bin/sh
# Builds the Fedora .rpm from this checkout in a clean container: apps/linux/packaging/build-rpm.sh [image] [outdir]
# The package works on every Fedora release with GTK 4 and gstreamer1-plugin-gtk4 (see rpm/beamcam.spec).
set -eu
image="${1:-fedora:45}"
here="$(cd "$(dirname "$0")" && pwd)"
out="$(mkdir -p "${2:-$here/dist}" && cd "${2:-$here/dist}" && pwd)"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
cp "$here/rpm/beamcam.spec" "$work/"
"$here/source-tarball.sh" "$work" >/dev/null
docker run --rm -v "$work:/work:ro" -v "$out:/out" "$image" sh -euc '
    dnf -q -y install rpm-build rpmlint >/dev/null 2>&1 || dnf -y install rpm-build rpmlint
    mkdir -p ~/rpmbuild/SOURCES && cp /work/*.tar.gz ~/rpmbuild/SOURCES/
    rpmbuild -bb --quiet /work/beamcam.spec
    rpmlint ~/rpmbuild/RPMS/noarch/*.rpm || true
    cp ~/rpmbuild/RPMS/noarch/*.rpm /out/ && chown "$(stat -c %u:%g /out)" /out/*.rpm
'
ls "$out"/*.rpm
