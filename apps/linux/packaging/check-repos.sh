#!/bin/sh
# Checks the repositories build-repos.sh made, the way a user gets updates from them: installs each
# PACKAGE (a .deb or .rpm, the same or an older version) in a clean container, turns updates on with
# the app's helper, updates Beamcam from SITE and checks it has the repository's version, then turns
# updates off and uninstalls: apps/linux/packaging/check-repos.sh SITE PACKAGE... [-- IMAGE]
# Only the repository's address is changed, to SITE on disk instead of the website.
set -eu
site="$(realpath "${1:?usage: check-repos.sh SITE PACKAGE... [-- IMAGE]}")"
shift
packages=""
image=""
while [ $# -gt 0 ]; do
    case "$1" in
        --) image="${2:?an image after --}"; shift 2 ;;
        *) packages="$packages $(realpath "$1")"; shift ;;
    esac
done
[ -n "$packages" ] || { echo "no PACKAGE given" >&2; exit 2; }

apt_check='
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq && apt-get install -qq -y --no-install-recommends "/pkg/$name" >/dev/null
# A repository that is not there yet would stop all of apt, so the helper refuses to add it.
if BEAMCAM_REPO_URL=file:///nowhere /usr/lib/beamcam/updates 2>/dev/null; then
    echo "the helper added a repository that is not there" >&2; exit 1
fi
[ ! -e /etc/apt/sources.list.d/beamcam.sources ]
/usr/lib/beamcam/updates
source=/etc/apt/sources.list.d/beamcam.sources
# Any warning, an unsigned or badly signed repository among them, fails here.
apt-get update -qq --error-on=any
want="$(sed -n "s/^Version: //p" /site/apt/Packages)"
apt-get install -qq -y --reinstall --no-install-recommends "beamcam=$want" >/dev/null
[ "$(dpkg-query -W -f "\${Version}" beamcam)" = "$want" ]
[ -e "$source" ] || { echo "the update removed $source" >&2; exit 1; }
/usr/lib/beamcam/updates --remove
[ ! -e "$source" ]
/usr/lib/beamcam/updates
apt-get remove -qq -y beamcam >/dev/null
[ ! -e "$source" ] || { echo "removing Beamcam left $source behind" >&2; exit 1; }
echo "$name updates to $want from the apt repository"
'

dnf_check='
dnf -q -y install --setopt=install_weak_deps=False "/pkg/$name" >/dev/null 2>&1
/usr/lib/beamcam/updates
repo=/etc/yum.repos.d/beamcam.repo
# What the software settings write to turn it off; turning updates on again must undo it.
mkdir -p /etc/dnf/repos.override.d
printf "[beamcam]\nenabled = 0\nproxy = http://192.0.2.1:3128\n" > /etc/dnf/repos.override.d/99-config_manager.repo
chmod 600 /etc/dnf/repos.override.d/99-config_manager.repo
if dnf -q repolist --enabled | grep -q "^beamcam "; then
    echo "dnf ignored the override, so this check proves nothing" >&2; exit 1
fi
/usr/lib/beamcam/updates
dnf -q repolist --enabled | grep -q "^beamcam "
grep -q "^proxy = http://192.0.2.1:3128" /etc/dnf/repos.override.d/99-config_manager.repo
[ "$(stat -c %a /etc/dnf/repos.override.d/99-config_manager.repo)" = 600 ]
rm /etc/dnf/repos.override.d/99-config_manager.repo
dnf -q makecache --repo beamcam
want="$(dnf -q repoquery --repo beamcam --qf "%{version}-%{release}" beamcam)"
if [ "$(rpm -q --qf "%{VERSION}-%{RELEASE}" beamcam)" = "$want" ]; then action=reinstall; else action=upgrade; fi
output="$(dnf -y --repo beamcam "$action" beamcam 2>&1)" || { echo "$output" >&2; exit 1; }
# The helper trusted the key already, as graphical software tools need it.
case "$output" in *"mporting"*) echo "dnf still had to trust a key: $output" >&2; exit 1 ;; esac
[ "$(rpm -q --qf "%{VERSION}-%{RELEASE}" beamcam)" = "$want" ]
[ -e "$repo" ] || { echo "the update removed $repo" >&2; exit 1; }
grep -qx "includepkgs=beamcam" "$repo"
scratch="$(mktemp -d)"
rpmkeys --root "$scratch" --import /site/beamcam.asc
key="$(rpmkeys --root "$scratch" --list | cut -d" " -f1)"
[ -n "$key" ] || { echo "no fingerprint in /site/beamcam.asc" >&2; exit 1; }
rpmkeys --list | grep -q "^$key "
/usr/lib/beamcam/updates --remove
[ ! -e "$repo" ]
! rpmkeys --list | grep -q "^$key " || { echo "turning updates off left the key trusted" >&2; exit 1; }
/usr/lib/beamcam/updates
dnf -q -y remove beamcam >/dev/null 2>&1
[ ! -e "$repo" ] || { echo "removing Beamcam left $repo behind" >&2; exit 1; }
echo "$name updates to $want from the dnf repository"
'

for package in $packages; do
    name="$(basename "$package")"
    case "$name" in
        *.deb) script="$apt_check" default=debian:13 ;;
        *.rpm) script="$dnf_check" default=fedora:45 ;;
        *) echo "not a .deb or .rpm: $package" >&2; exit 2 ;;
    esac
    # No stdin, so a question fails instead of waiting.
    docker run --rm -e name="$name" -e BEAMCAM_REPO_URL=file:///site -v "$site:/site:ro" -v "$package:/pkg/$name:ro" "${image:-$default}" \
        sh -euc "$script" </dev/null
done
