# Getting started

## What you need

- An Android phone (Android 9 or newer).
- A Linux or Windows 11 computer on the **same Wi-Fi** as the phone. A hotspot from the phone works too.

## Install

Download the files from the [newest release](https://github.com/JannikHerter/beamcam/releases/latest).
You need Beamcam on the phone and on the computer.

- **Phone:** open the release page on your phone and download `Beamcam-….apk`. Open the download.
  Android asks whether your browser may install apps: tap **Settings**, turn it on, go back and
  tap **Install**. If Play Protect asks about the app, tap **More details**, then **Install anyway**.
- **Windows 11:** download `Beamcam-Setup-….exe` and open it.
- **Debian or Ubuntu:** download the `.deb` file and open it, or run
  `sudo apt install ./beamcam_*.deb` in the folder you saved it to.
- **Arch or Manjaro:** download the `.pkg.tar.zst` file and run `sudo pacman -U beamcam-*.pkg.tar.zst`
  in the folder you saved it to.
- **Fedora:** download the `.rpm` file and open it, or run `sudo dnf install ./beamcam-*.rpm`
  in the folder you saved it to.

## On the computer

1. Open **Beamcam**.
2. On Linux, the first time, a bar at the top says **Set up the virtual camera**. Click **Set Up**
   and enter your password. This creates the camera other apps will see. You only do this once.
   - If it says the driver isn't installed, click **How to Install** and run the command it shows,
     then restart your computer.
3. On Windows, the installer sets up the camera; click **Yes** when Windows asks whether Beamcam
   may make changes. The camera is there while the Beamcam app is open, and video apps list it as
   **Beamcam (Windows Virtual Camera)**.
   - If Windows says it protected your PC, click **More info**, then **Run anyway**. It says this
     because the installer isn't signed yet.

## On the phone

1. Open **Beamcam** and tap **Allow camera**.
2. The phone now says **Ready** and waits for your computer.

## Connect

1. Your phone appears on the computer. Click **Connect**.
2. The phone asks **Allow “your computer” to use your camera?** Tap **Allow**. You're only asked
   once per computer. The phone and the computer show the same 6-digit code. Any computer can call
   itself anything, so the code is what counts: if the codes differ, tap **Don't allow**.
3. You see the picture on your computer. In your video call, browser or any other app that uses
   a camera, choose the camera **Beamcam**.

Next time, opening both apps is enough: the computer connects to your phone by itself. It only does
that with a phone you paired this way; other phones just appear in the list.

## While streaming

- **Switch Camera** on the computer, or the front/back buttons on the phone, change cameras.
- Turn your phone sideways for a wide picture. Upright gives a tall picture with black bars.
- You can lock the phone; streaming continues. Plug it in for long calls.
- **Disconnect** on the computer, or **Stop streaming** on the phone or in its notification, ends it.

## Updates

The first time you open Beamcam, it asks **Look for new versions?** If you tap **Yes**, the phone
and the Windows app ask Beamcam's download page which version is the newest (the phone when you
open the app, Windows when it starts and once a day); nothing else is sent. When there is one, tap **Update** on the phone and confirm, or click **Update Now** on
Windows (then **Yes**). The first time, the phone also asks you to allow Beamcam to install apps:
tap **Settings**, turn on **Allow from this source** and go back. Change your answer under **⋮ → Look for new versions** on the
phone or **☰ → Look for New Versions** on Windows.

On Linux, if you installed the `.deb` or `.rpm`, click **Yes** and enter your password. New
versions of Beamcam then come with your computer's other updates, in its software tool or with
`sudo apt update && sudo apt upgrade` or `sudo dnf upgrade`. Change your answer under
**☰ → Look for New Versions**. Beamcam 0.2.0 and older don't ask yet: install the newest `.deb` or
`.rpm` once by hand.

If you installed Beamcam on the phone from F-Droid, the app store updates it instead and Beamcam
doesn't ask. On Linux, the AUR updates Beamcam if you installed it from there. The Arch package
from the releases doesn't ask: install the newest `.pkg.tar.zst` from the
[releases](https://github.com/JannikHerter/beamcam/releases/latest) over the old one.

## Language

Beamcam uses the language of your phone and of your computer. To use another one, open **⋮ →
Language** on the phone or **☰ → Language** on the computer.

## Undo everything

- On the phone: **⋮ → Allowed computers** shows every computer you allowed. **Remove** one, or
  **Remove all**, and they have to ask again. **⋮ → Turn off** stops the phone from being found.
- On the computer: **☰ → Paired Phones** lets you **Forget** one phone or all of them. On Linux,
  **☰ → Remove Virtual Camera** removes the camera. On Windows the camera goes away when you close
  Beamcam, and uninstalling it in **Settings → Apps** removes the app and the camera. It keeps your
  paired phones in case you install it again, so **Forget** them first if you don't want that.
- Updates: **⋮ → Look for new versions** on the phone, **☰ → Look for New Versions** on the
  computer. On Linux, uninstalling Beamcam also removes its repository.
