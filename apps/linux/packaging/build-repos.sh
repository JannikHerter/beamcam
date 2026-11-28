#!/bin/sh
# Builds the website with Beamcam's apt and dnf repositories for one .deb and one .rpm, signed with
# the repository key (docs/internals/updates.md#linux):
#   BEAMCAM_REPO_KEY=secret-key.asc apps/linux/packaging/build-repos.sh PACKAGE.deb PACKAGE.rpm OUTDIR
# It refuses any key but the one in packaging/beamcam.asc, which the packages trust. With
# BEAMCAM_REPO_PREVIOUS set to the live website, the packages it lists stay downloadable, unlisted.
set -eu
deb="$(realpath "${1:?usage: build-repos.sh PACKAGE.deb PACKAGE.rpm OUTDIR}")"
rpm="$(realpath "${2:?usage: build-repos.sh PACKAGE.deb PACKAGE.rpm OUTDIR}")"
out="$(mkdir -p "${3:?usage: build-repos.sh PACKAGE.deb PACKAGE.rpm OUTDIR}" && realpath "$3")"
key="$(realpath "${BEAMCAM_REPO_KEY:?set BEAMCAM_REPO_KEY to the secret key file}")"
here="$(cd "$(dirname "$0")" && pwd)"

rm -rf "$out/apt" "$out/rpm"
mkdir -p "$out/apt" "$out/rpm"
cp "$deb" "$out/apt/"
cp "$rpm" "$out/rpm/"
cp "$here/beamcam.asc" "$out/beamcam.asc"
cat > "$out/index.html" <<'EOF'
<!doctype html>
<meta charset="utf-8">
<title>Beamcam packages</title>
<p>Beamcam's package repositories. Beamcam adds them when you turn on <b>Look for New Versions</b>;
see <a href="https://github.com/JannikHerter/beamcam">github.com/JannikHerter/beamcam</a>.
EOF

# Imports the key into a throwaway keyring and checks it is the one the packages trust.
gpg_setup='
export GNUPGHOME="$(mktemp -d)"
gpg --batch --quiet --import /key/secret.asc
fpr="$(gpg --batch --with-colons --list-secret-keys | awk -F: "/^fpr/ {print \$10; exit}")"
want="$(gpg --batch --with-colons --show-keys /site/beamcam.asc | awk -F: "/^fpr/ {print \$10; exit}")"
[ -n "$fpr" ] && [ "$fpr" = "$want" ] || { echo "The key is ${fpr:-missing}, not $want from beamcam.asc." >&2; exit 1; }
'
keydir="$(mktemp -d)"
trap 'rm -rf "$keydir"' EXIT
cp "$key" "$keydir/secret.asc"
chmod 644 "$keydir/secret.asc" # read by the container's root; the folder is the user's own

# A flat apt repository: apt reads InRelease, whose signature covers the hashes of Packages.
docker run --rm -v "$keydir:/key:ro" -v "$out:/site" debian:13 sh -euc "
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -qq && apt-get install -qq -y --no-install-recommends apt-utils gnupg xz-utils >/dev/null
    $gpg_setup
    cd /site/apt
    apt-ftparchive packages . > Packages
    xz -k9 Packages
    apt-ftparchive -o APT::FTPArchive::Release::Origin=Beamcam -o APT::FTPArchive::Release::Label=Beamcam \
        -o APT::FTPArchive::Release::Suite=stable -o APT::FTPArchive::Release::Codename=stable release . > Release
    gpg --batch --yes --local-user \"\$fpr\" --digest-algo SHA512 --clearsign -o InRelease Release
    gpg --batch --yes --local-user \"\$fpr\" --digest-algo SHA512 --armor --detach-sign -o Release.gpg Release
    chown -R \"\$(stat -c %u:%g /site)\" /site/apt
"

# dnf checks each package's signature (gpgcheck). The metadata stays unsigned, like Fedora's own:
# dnf would ask again to trust the key for it, and graphical software tools can't ask.
docker run --rm -v "$keydir:/key:ro" -v "$out:/site" fedora:45 sh -euc "
    dnf -q -y install rpm-sign createrepo_c gnupg2 >/dev/null 2>&1 || dnf -y install rpm-sign createrepo_c gnupg2
    $gpg_setup
    cd /site/rpm
    export GPG_TTY=/dev/null
    rpmsign --define \"_openpgp_sign_id \$fpr\" --define \"_gpg_name \$fpr\" --addsign ./*.rpm >/dev/null
    rpmkeys --import /site/beamcam.asc && rpmkeys --checksig ./*.rpm
    createrepo_c --quiet .
    chown -R \"\$(stat -c %u:%g /site)\" /site/rpm
"

# A computer whose metadata is from before this deploy asks for the files it lists; if they were
# gone, its whole upgrade would fail. Added after indexing, so only the new packages are offered.
previous="${BEAMCAM_REPO_PREVIOUS:-}"
keep() { # folder, file name from the live index
    case "$2" in ""|.*|*/*) return 0 ;; esac
    [ ! -e "$out/$1/$2" ] || return 0
    if curl -fsS -o "$out/$1/.$2.part" "$previous/$1/$2"; then
        mv "$out/$1/.$2.part" "$out/$1/$2"
    else
        rm -f "$out/$1/.$2.part"
        echo "warning: could not keep $1/$2 from $previous" >&2
    fi
}
if [ -n "$previous" ]; then
    if index="$(curl -fsS "$previous/apt/Packages")"; then
        for name in $(printf '%s\n' "$index" | sed -n 's|^Filename: \(\./\)\{0,1\}||p'); do keep apt "$name"; done
    else
        echo "no apt repository at $previous yet" >&2
    fi
    if primary="$(curl -fsS "$previous/rpm/repodata/repomd.xml" | grep -o 'repodata/[^"]*-primary\.xml\.[a-z]*')"; then
        case "$primary" in *.zst) unpack="zstd -dcq" ;; *.gz) unpack="gzip -dc" ;; *) unpack="xz -dc" ;; esac
        command -v "${unpack%% *}" >/dev/null || echo "warning: ${unpack%% *} is missing, so no .rpm is kept" >&2
        for name in $(curl -fsS "$previous/rpm/$primary" | $unpack | grep -o '<location href="[^"]*"' | sed 's/.*href="//; s/"$//'); do
            keep rpm "$name"
        done
    else
        echo "no dnf repository at $previous yet" >&2
    fi
fi
find "$out" -type f | sort
