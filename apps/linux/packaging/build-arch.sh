#!/bin/sh
# Builds the Arch package from this checkout in a clean container, with the AUR's PKGBUILD:
# apps/linux/packaging/build-arch.sh [image] [outdir]
set -eu
image="${1:-archlinux:latest}"
here="$(cd "$(dirname "$0")" && pwd)"
out="$(mkdir -p "${2:-$here/dist}" && cd "${2:-$here/dist}" && pwd)"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
cp "$here/arch/PKGBUILD" "$work/"
# makepkg uses a source file that is already next to the PKGBUILD instead of downloading it.
"$here/source-tarball.sh" "$work" >/dev/null
docker run --rm -v "$work:/work:ro" -v "$out:/out" "$image" bash -euc '
    pacman -Syu --noconfirm --needed base-devel namcap >/dev/null
    useradd -m builder && echo "builder ALL=(ALL) NOPASSWD: /usr/bin/pacman" > /etc/sudoers.d/builder
    cp -r /work /home/builder/pkg && chown -R builder /home/builder/pkg && cd /home/builder/pkg
    # -s installs depends and makedepends from the PKGBUILD, which also checks that they exist.
    su builder -c "PKGDEST=/home/builder/out makepkg -s --noconfirm --noprogressbar"
    # Arch writes .pkg.tar.zst, Arch Linux ARM .pkg.tar.xz.
    namcap PKGBUILD /home/builder/out/*.pkg.tar.* || true
    cp /home/builder/out/*.pkg.tar.* /out/ && chown "$(stat -c %u:%g /out)" /out/*.pkg.tar.*
'
ls "$out"/*.pkg.tar.*
