#!/bin/sh
# Checks that every app and package says the same version: scripts/check-version.sh 0.2.0
# The release workflow runs it for the tag, so a forgotten file stops the release.
set -eu
want="${1:?usage: check-version.sh VERSION}"
root="$(cd "$(dirname "$0")/.." && pwd)"
dotted="$(echo "$want" | tr . ,),0"
failed=0

check() { # file, then the text that must appear in it
    if ! grep -qF -- "$2" "$root/$1"; then
        echo "$1: expected \"$2\"" >&2
        failed=1
    fi
}

check apps/android/app/build.gradle.kts "versionName = \"$want\""
check apps/linux/pyproject.toml "version = \"$want\""
check apps/linux/src/beamcam/__init__.py "__version__ = \"$want\""
check apps/linux/packaging/arch/PKGBUILD "pkgver=$want"
check apps/linux/data/org.beamcam.Beamcam.metainfo.xml "<release version=\"$want\""
check apps/windows/Directory.Build.props "<Version>$want</Version>"
check apps/windows/installer/Beamcam.iss "#define AppVersion \"$want\""
check apps/windows/native/src/version.rc "FILEVERSION $dotted"
check apps/windows/native/src/version.rc "\"ProductVersion\", \"$want\""
check apps/windows/src/Beamcam/app.manifest "version=\"$want.0\""
check CHANGELOG.md "## $want"
if [ "$(head -1 "$root/apps/linux/packaging/debian/changelog" | sed -n 's/^beamcam (\([^-]*\)-.*/\1/p')" != "$want" ]; then
    echo "apps/linux/packaging/debian/changelog: the first entry is not $want" >&2
    failed=1
fi

[ "$failed" = 0 ] && echo "Everything says $want."
exit "$failed"
