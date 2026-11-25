# Troubleshooting

## My phone doesn't show up on the computer

- Both must be on the **same Wi-Fi**. Guest networks and some office or school networks block
  devices from seeing each other; use your home Wi-Fi or your phone's hotspot.
- Keep the Beamcam app open on the phone until it connects.
- Use **Connect by Address…** on the computer and type the address shown on the phone
  (for example `192.168.1.23`).
- A firewall can block discovery. Allow mDNS (UDP port 5353). On Fedora:
  `sudo firewall-cmd --add-service=mdns --permanent && sudo firewall-cmd --reload`.
  On Windows the installer allows it for Beamcam; another security program may still block it.

## “Couldn't reach the phone”

The computer found no phone at that address. Check the address on the phone screen and that both
are on the same Wi-Fi. VPNs on the computer can also get in the way; try turning yours off.

## My video app doesn't list “Beamcam”

- Start streaming first, then (re)open the camera list in the app. Many apps only list the
  camera while the phone is streaming.
- **Windows:** the camera exists while the Beamcam app is open. Open Beamcam first, then the other
  app. If it still isn't there, check **Settings → Privacy & security → Camera**: camera access and
  “Let desktop apps access your camera” must be on.
- **Firefox:** if the camera is missing (seen on some Fedora builds), open `about:config` and set
  `media.webrtc.camera.allow-pipewire` to `false`, then restart Firefox.
- After installing the driver, restart your computer once.

## Secure Boot blocks the virtual camera

With Secure Boot on, your computer only loads drivers it trusts. If setting up the virtual camera
says Secure Boot blocked the driver, allow the driver's key once (**Open Guide** in that message
leads here):

1. Open a terminal, run the line for your system and pick a one-time password when it asks:
   - **Fedora:** `sudo kmodgenca -a && sudo akmods --rebuild && sudo mokutil --import /etc/pki/akmods/certs/public_key.der`
   - **Ubuntu, Linux Mint, Pop!_OS:** `sudo mokutil --import /var/lib/shim-signed/mok/MOK.der`
   - **Debian:** `sudo mokutil --import /var/lib/dkms/mok.pub`
   - **Other systems:** search for “Secure Boot” and “v4l2loopback” together with your system's
     name, or turn Secure Boot off in your computer's firmware settings.
2. Restart and watch the screen. When a blue screen says **Press any key**, press a key within
   10 seconds. Choose **Enroll MOK**, then **Continue** and **Yes**, type the password, and choose
   **Reboot**. If your computer starts normally instead, do step 1 again.
3. Open Beamcam and click **Set Up** again.

## The picture is sideways or upside down

Turn the phone once; the picture follows the phone. If your phone's rotation lock is on, the
picture still follows how you hold it.

## The picture stutters or freezes

- Move closer to the Wi-Fi router, or use the phone's hotspot. 5 GHz Wi-Fi works best.
- If it freezes for several seconds and the computer says it is reconnecting, the phone briefly
  left the Wi-Fi. Turn off battery saving for Beamcam on the phone
  (Settings → Apps → Beamcam → Battery → Unrestricted).
