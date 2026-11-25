#!/bin/sh
# Packs this checkout like the release tarball GitHub serves (beamcam-X.Y.Z/ with LICENSE and
# apps/linux), so the Arch and Fedora recipes build local changes the way they build a release.
# Usage: apps/linux/packaging/source-tarball.sh OUTDIR; prints the tarball's path.
set -eu
out="${1:?usage: source-tarball.sh OUTDIR}"
root="$(cd "$(dirname "$0")/../../.." && pwd)"
version="$(sed -n 's/^version = "\(.*\)"$/\1/p' "$root/apps/linux/pyproject.toml")"
tarball="$out/beamcam-$version.tar.gz"
# The files git tracks, as they are on disk now: a release holds the same files once committed,
# so a new file needs `git add` before it is packaged.
git -C "$root" ls-files -z -- LICENSE apps/linux \
    | (cd "$root" && xargs -0 sh -c 'for f; do [ -e "$f" ] && printf "%s\0" "$f"; done' sh) \
    | tar -C "$root" --null -T - --owner=0 --group=0 --transform "s,^,beamcam-$version/," -czf "$tarball"
tar -tzf "$tarball" "beamcam-$version/apps/linux/pyproject.toml" >/dev/null 2>&1 || { echo "$tarball is incomplete" >&2; exit 1; }
echo "$tarball"
