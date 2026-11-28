#!/bin/sh
# Writes update.json and update.json.sig for a release (see docs/internals/updates.md):
#   scripts/sign-update.sh VERSION PHONE.apk WINDOWS-SETUP.exe OUT-DIR
# The key comes from the environment like the phone app's: BEAMCAM_KEYSTORE (the .p12) and
# BEAMCAM_KEYSTORE_PASSWORD. Set RELEASE_CERT_SHA256 to refuse any other key.
set -eu
version="${1:?usage: sign-update.sh VERSION APK SETUP-EXE OUT-DIR}"
apk="${2:?}"
exe="${3:?}"
out="${4:?}"
: "${BEAMCAM_KEYSTORE:?the release key (.p12)}" "${BEAMCAM_KEYSTORE_PASSWORD:?the release key password}"

work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
openssl pkcs12 -in "$BEAMCAM_KEYSTORE" -passin env:BEAMCAM_KEYSTORE_PASSWORD -nocerts -nodes -out "$work/key.pem"
openssl pkcs12 -in "$BEAMCAM_KEYSTORE" -passin env:BEAMCAM_KEYSTORE_PASSWORD -nokeys -clcerts | openssl x509 -out "$work/cert.pem"
if [ -n "${RELEASE_CERT_SHA256:-}" ]; then
    digest="$(openssl x509 -in "$work/cert.pem" -outform DER | sha256sum | cut -d' ' -f1)"
    [ "$digest" = "$RELEASE_CERT_SHA256" ] || { echo "The key's certificate is $digest, not the release key." >&2; exit 1; }
fi

mkdir -p "$out"
python3 - "$version" "$apk" "$exe" > "$out/update.json" <<'EOF'
import hashlib, json, os, re, sys

version, apk, exe = sys.argv[1:]
if not re.fullmatch(r"\d+\.\d+\.\d+", version):
    sys.exit(f"{version} is a pre-release or not X.Y.Z; apps only take X.Y.Z")

def entry(path):
    with open(path, "rb") as f:
        data = f.read()
    return {"name": os.path.basename(path), "size": len(data), "sha256": hashlib.sha256(data).hexdigest()}

print(json.dumps({"version": version, "files": {"android": entry(apk), "windows": entry(exe)}}, indent=2))
EOF

{ printf 'beamcam-update-v1\n'; cat "$out/update.json"; } > "$work/signed"
openssl dgst -sha256 -sign "$work/key.pem" -out "$out/update.json.sig" "$work/signed"
openssl x509 -in "$work/cert.pem" -pubkey -noout > "$work/public.pem"
openssl dgst -sha256 -verify "$work/public.pem" -signature "$out/update.json.sig" "$work/signed"
