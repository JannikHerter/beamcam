#!/bin/sh
# Builds the .deb in a clean container: apps/linux/packaging/build-deb.sh [image] [outdir]
# Debian 13 and Ubuntu 26.04 are the oldest releases that ship gstreamer1.0-gtk4.
set -eu
image="${1:-debian:13}"
here="$(cd "$(dirname "$0")" && pwd)"
out="$(mkdir -p "${2:-$here/dist}" && cd "${2:-$here/dist}" && pwd)"
linux="$(dirname "$here")"
docker run --rm -v "$linux:/src:ro" -v "$out:/out" "$image" sh -euc '
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -qq
    apt-get install -qq -y --no-install-recommends build-essential debhelper dh-python \
        pybuild-plugin-pyproject python3-all python3-setuptools >/dev/null
    mkdir /build && cp -r /src /build/beamcam && cd /build/beamcam
    rm -rf build dist packaging/dist src/*.egg-info && cp -r packaging/debian debian
    dpkg-buildpackage -us -uc -b
    cp ../*.deb /out/ && chown "$(stat -c %u:%g /out)" /out/*.deb
'
ls "$out"/*.deb
