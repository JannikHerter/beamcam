# Updates

The Windows app and the phone app can update themselves from the GitHub release, after the user
said yes to it. This page is the contract between the release workflow and the apps.

## Who updates how

| Installed with | Updated by |
|---|---|
| Phone: the `.apk` from the release (or `adb`) | the phone app, once the user turned it on |
| Phone: F-Droid, Google Play or any other store | that store only; the app hides everything about updates |
| Windows: `Beamcam-Setup-….exe` | the Windows app, once the user turned it on |
| Linux | the package manager (no in-app updates) |

The phone tells the two apart by what Android recorded about its install (`InstallSourceInfo`):
in-app updates are offered only when both the installer and (Android 11+) the app that started the
install are nobody or `adb`, Beamcam itself, or the system's package installer (a package name
ending in `.packageinstaller`). Every store records its own name, so any store, known or not, keeps
the updates to itself. Android lets an app read its own initiator after that app is uninstalled
(AOSP `InstallSource.removeInstallerPackage`), so removing F-Droid does not hand its install over
to the app. The app must also be signed with the release key, or Android could not install the
update over it; debug builds never offer updates. F-Droid publishes our own signed APK (see
[releases](../operations/development.md#releases)), so its installs pass that check, and the same
file comes from both places: a build switch cannot tell them apart.

What the app cannot see:

- A store client set to hand APKs to the system's install dialog (a "legacy" installer option in
  some F-Droid clients) looks like a downloaded APK. Android only tells apps holding
  `INSTALL_PACKAGES` which app the APK came from. The default modes of the F-Droid client,
  Droid-ify and Neo Store install themselves and record their own name.
- On Android 9 and 10 only the installer is known, and Android forgets it when that store is
  uninstalled; Beamcam then offers its own updates, since nothing else updates it any more.

## Asking first

Nothing is fetched until the user answered **Yes** to the one question both apps ask (on the
phone's main screen, in a bar on Windows). **No** is as easy as **Yes**, and the menu changes the
answer later. This is also F-Droid's rule for apps that download code.

Checking means two requests to github.com and nothing else: no id, no device details, only a
`User-Agent: Beamcam/<version>`. Windows checks when it starts and then once a day; the phone when
the app is opened, at most once a day while it keeps running. Installing always takes one click or
tap from the user, and the phone never offers it, or shows Android's confirmation, while streaming.
On Windows, Setup asks for administrator rights and then closes the app itself; if the user says
no, the app stays open and offers the update again.

## The files

Each release has `update.json` and `update.json.sig` next to the downloads, made by
`scripts/sign-update.sh` in the release workflow. The apps fetch

- `https://github.com/JannikHerter/beamcam/releases/latest/download/update.json` and the same URL
  with `.sig`. "latest" skips pre-releases, and the apps only take `X.Y.Z`.
- the file itself from `https://github.com/JannikHerter/beamcam/releases/download/v<version>/<name>`.

```json
{
  "version": "0.3.0",
  "files": {
    "android": { "name": "Beamcam-0.3.0.apk", "size": 4567890, "sha256": "…" },
    "windows": { "name": "Beamcam-Setup-0.3.0.exe", "size": 61234567, "sha256": "…" }
  }
}
```

- `version` is `X.Y.Z` (numbers only). An app offers it only when it is higher than its own.
- `name` is a plain file name (letters, digits, `.`, `_`, `-`); the app builds the URL itself, so
  `update.json` cannot send it anywhere else.
- `sha256` is lowercase hex. The app checks `size` and `sha256` before installing and deletes the
  file if either is wrong. Unknown fields and platforms are ignored.

`update.json.sig` is an RSASSA-PKCS1-v1_5 SHA-256 signature, made with the release key (the one
that signs the phone app), over the bytes `beamcam-update-v1\n` followed by the exact bytes of
`update.json`. The apps carry the key's public half and drop an update whose signature does not
verify. The prefix keeps these signatures apart from the key's APK signatures. Check one by hand:

```sh
openssl pkcs12 -in beamcam-release.p12 -nokeys -clcerts | openssl x509 -pubkey -noout > release.pub
{ printf 'beamcam-update-v1\n'; cat update.json; } | openssl dgst -sha256 -verify release.pub -signature update.json.sig
```

Why sign at all when the download is HTTPS: HTTPS only proves that GitHub sent the file. The
signature means a stolen GitHub account cannot ship an update; the Windows installer is not
code-signed, so this is its only protection. Android additionally refuses an APK that is not signed
with the installed app's key.
