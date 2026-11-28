# Updates

The Windows app and the phone app can update themselves from the GitHub release, and on Linux the
computer's own updates can bring new versions, after the user said yes to it. This page is the
contract between the release workflows and the apps.

## Who updates how

| Installed with | Updated by |
|---|---|
| Phone: the `.apk` from the release (or `adb`) | the phone app, once the user turned it on |
| Phone: F-Droid, Google Play or any other store | that store only; the app hides everything about updates |
| Windows: `Beamcam-Setup-….exe` | the Windows app, once the user turned it on |
| Linux: the `.deb` or `.rpm` from the release | the computer's own updates, from Beamcam's repository, once the user turned it on |
| Linux: the AUR | the AUR |

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

Nothing is fetched until the user answered **Yes** to the one question every app asks (on the
phone's main screen, in a bar on Windows and Linux). **No** is as easy as **Yes**, and the menu
changes the answer later. This is also F-Droid's rule for apps that download code.

On the phone and Windows, checking means two requests to github.com and nothing else: no id, no
device details, only a `User-Agent: Beamcam/<version>`. Windows checks when it starts and then once a day; the phone when
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

## Linux

The Linux app never downloads anything itself: a package installed as root shouldn't replace
itself behind the package manager's back. Instead, **Yes** adds Beamcam's package repository, and
the computer's software tool then offers new versions with its other updates. It fetches from
`https://jannikherter.github.io/beamcam/` whenever it looks for updates, with its own User-Agent
(dnf's names the distribution, its version and the architecture); Beamcam adds nothing to it.

- Only the `.deb` and `.rpm` ask. They install `/usr/lib/beamcam/updates`, which the window runs
  through pkexec (polkit action `org.beamcam.Beamcam.updates`), and the repository key:
  `/usr/share/keyrings/beamcam.asc` in the `.deb`, `/etc/pki/rpm-gpg/RPM-GPG-KEY-beamcam` in the
  `.rpm`. The AUR package, pip and a checkout have no helper and don't ask.
- The helper writes `/etc/apt/sources.list.d/beamcam.sources` (a flat repository, `Suites: ./`,
  trusting only that key) or `/etc/yum.repos.d/beamcam.repo` (`gpgcheck=1`, `includepkgs=beamcam`,
  `skip_if_unavailable=1` so an outage of the site never blocks the computer's other updates;
  it also imports the key into rpm, because graphical software tools can't ask whether to trust a
  new key). `--remove` deletes the file and the key in rpm; uninstalling Beamcam deletes the file
  (the `.deb`'s `postrm`, the `.rpm`'s `%ghost`) but leaves the key in rpm, since rpm is locked
  while it uninstalls. apt has no `includepkgs`: it would take any package the repository signs.
  The repository only ever holds `beamcam`, and whoever could sign another package could as well
  sign a harmful `beamcam`, so a pin would add a file without adding safety.
- Before adding the apt source, the helper fetches `InRelease` once. apt stops updating anything
  while one of its repositories is missing, so the helper refuses with "can't be reached" rather
  than break the computer's other updates.
- The window shows updates as on while either file exists and isn't disabled (`Enabled: no`,
  `enabled=0`), whoever added it. An Ubuntu release upgrade disables or moves other repositories
  without asking; the window then asks again, unless this user said **No**. Turning it off in the
  system's software settings looks the same, so that user sees the question once more too.
  GNOME Software and `dnf config-manager` turn the dnf repository off with `enabled=0` for
  `beamcam` in `/etc/dnf/repos.override.d/99-config_manager.repo`; the window counts that as off
  too, and the helper removes that `enabled` (keeping any other setting there, and the file's
  owner and mode) whenever it turns updates on or off. Either way, the override outranks the file.
- **No** is kept per user in `config.json` (`check_updates`); the menu item **Look for New Versions**
  turns the repository on or off for the whole computer, and asks for the password each time.

The website offers only the newest release: `apt/` (the `.deb`, `Packages`, and `InRelease` signed
with the repository key) and `rpm/` (the `.rpm` signed with the same key, and `repodata/`). The
packages the previous deploy listed stay there, unlisted, for computers that refreshed before it:
without them their next upgrade would fail on a missing file. The dnf
metadata is unsigned, like Fedora's own; each package's signature is what dnf checks. After each
release, `.github/workflows/repos.yml` takes the `.deb` and `.rpm` the Release run built (not the
release's downloads, which whoever may edit the release could replace), builds the site with
`apps/linux/packaging/build-repos.sh`, updates an installed package from it in clean Debian and
Fedora containers (`check-repos.sh`), and publishes it on GitHub Pages. It only publishes GitHub's
latest release: never a pre-release, and never an older one, which would replace newer packages.
It only signs files from a run of `release.yml` that the push of that same tag started, and checks
again right before deploying that no newer release came out meanwhile.
Right after a deploy, GitHub Pages' cache can serve the new `InRelease` with the old `Packages`
for a few minutes; apt then reports a hash mismatch once and succeeds on its next update.
Publishing the same release again signs its `.rpm` anew and drops what the deploy before it kept;
computers that refreshed in between fail once on their next upgrade, until they refresh again.

The repository key is an OpenPGP key of its own (RSA 4096, no expiry), not the release key: apt and
rpm only take OpenPGP keys. Its public half is `apps/linux/packaging/beamcam.asc`, which the
packages install and `build-repos.sh` refuses to sign with any other key. The workflow gets the
secret half from `BEAMCAM_REPO_KEY` in the `linux-repos` environment, which only main may use: with
the key, anyone could ship a package that runs as root on every computer that said yes
([setup](../operations/development.md#linux-repositories)).
