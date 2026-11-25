#!/bin/sh
# Updates a checkout of the AUR package to the released version in the PKGBUILD next to this file.
# Run it on Arch or Manjaro after the release is published:
#   git clone ssh://aur@aur.archlinux.org/beamcam.git ~/aur-beamcam
#   apps/linux/packaging/arch/update-aur.sh ~/aur-beamcam
# then review, commit and push in that checkout.
set -eu
aur="$(cd "${1:?usage: update-aur.sh AUR-CHECKOUT}" && pwd)"
here="$(cd "$(dirname "$0")" && pwd)"
version="$(sed -n 's/^pkgver=//p' "$here/PKGBUILD")"
tarball="$(mktemp)"
trap 'rm -f "$tarball"' EXIT
curl -fsSL -o "$tarball" "https://github.com/JannikHerter/beamcam/archive/refs/tags/v$version.tar.gz"
sum="$(sha256sum "$tarball" | cut -d' ' -f1)"
# The comment on top is about this repository, so the AUR copy starts at pkgname.
sed -n "/^pkgname=/,\$p" "$here/PKGBUILD" | sed "s/^sha256sums=('SKIP')$/sha256sums=('$sum')/" > "$aur/PKGBUILD"
grep -q "^sha256sums=('$sum')$" "$aur/PKGBUILD" || { echo "PKGBUILD has no sha256sums=('SKIP') line" >&2; exit 1; }
(cd "$aur" && makepkg --printsrcinfo > .SRCINFO)
echo "Updated $aur to $version. Check it with: cd $aur && makepkg -si && git diff"
