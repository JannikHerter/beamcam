# Fedora package, built by packaging/build-rpm.sh. One file serves every Fedora release: the app
# lives in /usr/share/beamcam instead of the versioned site-packages, so it does not depend on
# one Python version (Fedora 43 and 44 have 3.14, Fedora 45 has 3.15).
Name:           beamcam
Version:        0.2.0
Release:        1
Summary:        Use your Android phone as a wireless webcam
License:        GPL-3.0-or-later
URL:            https://github.com/JannikHerter/beamcam
Source0:        %{url}/archive/refs/tags/v%{version}.tar.gz#/%{name}-%{version}.tar.gz
BuildArch:      noarch

Requires:       python3 >= 3.10
Requires:       python3-gobject
Requires:       python3-cryptography
Requires:       gtk4
Requires:       libadwaita
Requires:       polkit
Requires:       gstreamer1-plugins-base
Requires:       gstreamer1-plugins-good
Requires:       gstreamer1-plugins-bad-free
Requires:       gstreamer1-plugin-gtk4
# Fedora's FFmpeg cannot decode H.264. OpenH264 can; it comes from Cisco's repository, which
# Fedora enables by default.
Requires:       gstreamer1-plugin-openh264
# From RPM Fusion; skipped when that is not set up, and the app then says how to get it.
Recommends:     akmod-v4l2loopback

%description
Turn your Android phone into a wireless webcam for video calls, your browser
and any other app that uses a camera. Open Beamcam on the phone and on the
computer, click Connect, and pick the "Beamcam" camera.

%prep
%autosetup -n %{name}-%{version}

%build

%install
cd apps/linux
mkdir -p %{buildroot}%{_datadir}/%{name} %{buildroot}%{_bindir}
cp -r src/beamcam %{buildroot}%{_datadir}/%{name}/
find %{buildroot}%{_datadir}/%{name} -name __pycache__ -prune -exec rm -rf {} +
chmod 755 %{buildroot}%{_datadir}/%{name}/beamcam/data/setup-virtual-camera
cat > %{buildroot}%{_bindir}/beamcam <<'EOF'
#!/usr/bin/python3 -sB
import sys
sys.path.insert(0, "%{_datadir}/%{name}")
from beamcam.cli import main
sys.exit(main())
EOF
chmod 755 %{buildroot}%{_bindir}/beamcam
sh packaging/install-data.sh %{buildroot}

%files
%license LICENSE
%{_bindir}/beamcam
%{_datadir}/%{name}/
%{_prefix}/lib/%{name}/
%{_datadir}/applications/org.beamcam.Beamcam.desktop
%{_datadir}/icons/hicolor/scalable/apps/org.beamcam.Beamcam.svg
%{_datadir}/metainfo/org.beamcam.Beamcam.metainfo.xml
%{_datadir}/polkit-1/actions/org.beamcam.Beamcam.policy

%changelog
* Wed Nov 25 2026 Beamcam developers <linuxisbetter@tutamail.com> - 0.2.0-1
- First Fedora package. Helps when Secure Boot blocks the virtual camera driver.

* Wed Nov 25 2026 Beamcam developers <linuxisbetter@tutamail.com> - 0.1.0-1
- The first public version.
