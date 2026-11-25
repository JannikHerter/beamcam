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

check apps/linux/packaging/rpm/beamcam.spec "Version:        $want"
check apps/linux/packaging/rpm/beamcam.spec "> - $want-1"

# Phones and F-Droid only take a higher versionCode than the last release's, and F-Droid shows
# the store changelog named after it.
code_of() { sed -n 's/^ *versionCode = \([0-9]*\)$/\1/p'; }
code="$(code_of < "$root/apps/android/app/build.gradle.kts")"
for lang in en-US de-DE; do
    changelog="fastlane/metadata/android/$lang/changelogs/$code.txt"
    if [ ! -s "$root/$changelog" ]; then
        echo "$changelog: missing (the phone's store changelog)" >&2
        failed=1
    elif [ "$(LC_ALL=C.UTF-8 wc -m < "$root/$changelog")" -gt 500 ]; then
        echo "$changelog: longer than F-Droid's 500 characters" >&2
        failed=1
    fi
done
if previous="$(git -C "$root" describe --tags --abbrev=0 --match 'v[0-9]*' --exclude "v$want" HEAD 2>/dev/null)"; then
    previous_code="$(git -C "$root" show "$previous:apps/android/app/build.gradle.kts" | code_of)"
    if [ "$code" -le "$previous_code" ]; then
        echo "apps/android/app/build.gradle.kts: versionCode $code must be higher than $previous_code ($previous)" >&2
        failed=1
    fi
else
    echo "No earlier release tag found, so versionCode was not compared." >&2
fi

[ "$failed" = 0 ] && echo "Everything says $want."
exit "$failed"
