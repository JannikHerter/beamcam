"""The GTK window. Thin on purpose: discovery, sessions and video live in their own modules."""

from __future__ import annotations

import logging
import threading

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gst", "1.0")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk  # noqa: E402

from beamcam import APP_ID, __version__, virtualcam  # noqa: E402
from beamcam.client import CLOSED, StreamInfo, end_message, parse_address  # noqa: E402
from beamcam.config import Config, computer_name  # noqa: E402
from beamcam.controller import Controller  # noqa: E402
from beamcam.mdns import Browser, Phone  # noqa: E402
from beamcam.pipeline import Pipeline, missing_elements  # noqa: E402
from beamcam.reconnect import Reconnector, Target  # noqa: E402

log = logging.getLogger(__name__)



class Window(Adw.ApplicationWindow):
    def __init__(self, app: Adw.Application) -> None:
        super().__init__(application=app, title="Beamcam", default_width=760, default_height=580)
        self.set_size_request(360, 420)
        self.config = Config.load()
        self.phones: list[Phone] = []
        self.controller: Controller | None = None
        self.auto_connect_allowed = True
        self.reconnector = Reconnector()
        self.reconnect_source = 0

        self.toasts = Adw.ToastOverlay()
        self.banner = Adw.Banner(revealed=False)
        self.banner.connect("button-clicked", self._on_banner_button)
        self.stack = Adw.ViewStack(vexpand=True)
        self.stack.add_named(self._build_phones_page(), "phones")
        self.stack.add_named(self._build_connecting_page(), "connecting")
        self.stack.add_named(self._build_streaming_page(), "streaming")

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        content.append(self.banner)
        content.append(self.stack)
        self.toasts.set_child(content)

        header = Adw.HeaderBar()
        menu = Gio.Menu()
        menu.append("Connect by Address…", "win.connect-address")
        menu.append("Forget Paired Phones…", "win.forget-phones")
        menu.append("Remove Virtual Camera…", "win.remove-camera")
        menu.append("About Beamcam", "win.about")
        header.pack_end(Gtk.MenuButton(icon_name="open-menu-symbolic", menu_model=menu, tooltip_text="Main Menu"))
        view = Adw.ToolbarView()
        view.add_top_bar(header)
        view.set_content(self.toasts)
        self.set_content(view)

        for name, callback in (
            ("connect-address", self._ask_address),
            ("forget-phones", self._confirm_forget_phones),
            ("remove-camera", self._confirm_remove_camera),
            ("about", self._show_about),
        ):
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", lambda *_a, cb=callback: cb())
            self.add_action(action)

        self.connect("close-request", self._on_close)
        self.refresh_camera_state()
        self._render_phones()
        self.browser = Browser(lambda phones: GLib.idle_add(self._on_phones, phones))
        self.browser.start()

        missing = missing_elements(preview=True)
        if missing:
            self.toast(f"Missing video components: {', '.join(missing)}. Install gst-plugins-good, gst-libav and gst-plugin-gtk4.")

    # Pages

    def _build_phones_page(self) -> Gtk.Widget:
        self.phones_status = Adw.StatusPage(icon_name="camera-web-symbolic", vexpand=True)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18, halign=Gtk.Align.CENTER)
        self.searching_spinner = Gtk.Spinner(spinning=True, width_request=32, height_request=32)
        box.append(self.searching_spinner)
        self.phone_list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE, width_request=340)
        self.phone_list.add_css_class("boxed-list")
        box.append(self.phone_list)
        manual = Gtk.Button(label="Connect by Address…", halign=Gtk.Align.CENTER)
        manual.add_css_class("pill")
        manual.connect("clicked", lambda _b: self._ask_address())
        box.append(manual)
        self.phones_status.set_child(box)
        return self.phones_status

    def _build_connecting_page(self) -> Gtk.Widget:
        self.connecting_status = Adw.StatusPage(icon_name="phone-symbolic", vexpand=True)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=24, halign=Gtk.Align.CENTER)
        box.append(Gtk.Spinner(spinning=True, width_request=32, height_request=32))
        cancel = Gtk.Button(label="Cancel")
        cancel.add_css_class("pill")
        cancel.connect("clicked", lambda _b: self.disconnect())
        box.append(cancel)
        self.connecting_status.set_child(box)
        return self.connecting_status

    def _build_streaming_page(self) -> Gtk.Widget:
        self.picture = Gtk.Picture(content_fit=Gtk.ContentFit.CONTAIN, hexpand=True, vexpand=True)
        frame = Gtk.Box(css_classes=["preview"])
        frame.append(self.picture)
        css = Gtk.CssProvider()
        css.load_from_string(".preview { background: black; }")
        Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

        self.streaming_title = Gtk.Label(xalign=0, css_classes=["heading"], ellipsize=3)
        self.streaming_hint = Gtk.Label(xalign=0, wrap=True, css_classes=["dim-label"])
        labels = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, hexpand=True, valign=Gtk.Align.CENTER)
        labels.append(self.streaming_title)
        labels.append(self.streaming_hint)

        switch = Gtk.Button(label="Switch Camera", valign=Gtk.Align.CENTER)
        switch.connect("clicked", lambda _b: self._switch_camera())
        stop = Gtk.Button(label="Disconnect", valign=Gtk.Align.CENTER)
        stop.add_css_class("destructive-action")
        stop.connect("clicked", lambda _b: self.disconnect())

        bar = Gtk.Box(spacing=12, margin_start=18, margin_end=18, margin_top=12, margin_bottom=12)
        bar.append(labels)
        bar.append(switch)
        bar.append(stop)

        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        page.append(frame)
        page.append(bar)
        self.facing = "front"
        return page

    # Virtual camera

    def refresh_camera_state(self) -> None:
        camera = virtualcam.find_camera()
        self.camera = camera if camera and camera.writable else None
        if camera and not camera.writable:
            self._banner("Your user can’t write to the virtual camera.", "Details", "permission")
        elif camera:
            self.banner.set_revealed(False)
        elif virtualcam.module_installed():
            self._banner("Set up the virtual camera so other apps can use your phone.", "Set Up", "setup")
        else:
            self._banner("The virtual camera driver (v4l2loopback) isn’t installed.", "How to Install", "install")

    def _banner(self, title: str, button: str, kind: str) -> None:
        self.banner.set_title(title)
        self.banner.set_button_label(button)
        self.banner_kind = kind
        self.banner.set_revealed(True)

    def _on_banner_button(self, _banner) -> None:
        if self.banner_kind == "setup":
            self._run_camera_helper(remove=False)
        elif self.banner_kind == "install":
            self._show_install_help()
        else:
            self._alert(
                "No Permission",
                f"Beamcam can’t write to {virtualcam.find_camera().path}. "
                "Add your user to the “video” group, then log out and back in:\n\nsudo usermod -aG video $USER",
            )

    def _run_camera_helper(self, remove: bool) -> None:
        self.banner.set_sensitive(False)

        def work() -> None:
            ok, error = virtualcam.run_helper(remove=remove)
            GLib.idle_add(done, ok, error)

        def done(ok: bool, error: str) -> None:
            self.banner.set_sensitive(True)
            self.refresh_camera_state()
            if ok:
                self.toast("Virtual camera removed." if remove else "Virtual camera is ready.")
            elif error:
                self._alert("Couldn’t Change the Virtual Camera", error)

        threading.Thread(target=work, daemon=True).start()

    def _show_install_help(self) -> None:
        command = virtualcam.install_command()
        body = (
            "Beamcam needs the v4l2loopback driver to create a camera other apps can use. "
            + ("Install it by running this in a terminal, then restart your computer:" if command else
               "Install the “v4l2loopback” package with your distribution’s software tool, then restart your computer.")
        )
        dialog = Adw.AlertDialog(heading="Install the Virtual Camera Driver", body=body)
        if command:
            label = Gtk.Label(label=command, selectable=True, wrap=True, css_classes=["monospace", "card"])
            label.set_margin_top(6)
            for side in ("start", "end", "bottom"):
                getattr(label, f"set_margin_{side}")(6)
            dialog.set_extra_child(label)
            dialog.add_response("copy", "Copy Command")
        dialog.add_response("check", "Check Again")
        dialog.add_response("close", "Close")
        dialog.set_default_response("check")

        def on_response(_dialog, response: str) -> None:
            if response == "copy" and command:
                self.get_clipboard().set(command)
                self.toast("Command copied.")
            self.refresh_camera_state()

        dialog.connect("response", on_response)
        dialog.present(self)

    def _confirm_forget_phones(self) -> None:
        dialog = Adw.AlertDialog(
            heading="Forget Paired Phones?",
            body="This computer will no longer connect to your phones by itself. To use a phone again, "
            "click Connect and tap Allow on the phone.",
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("forget", "Forget")
        dialog.set_response_appearance("forget", Adw.ResponseAppearance.DESTRUCTIVE)

        def on_response(_dialog, response: str) -> None:
            if response == "forget":
                self.disconnect()
                self.config.forget_all()
                self._render_phones()
                self.toast("Paired phones forgotten.")

        dialog.connect("response", on_response)
        dialog.present(self)

    def _confirm_remove_camera(self) -> None:
        dialog = Adw.AlertDialog(
            heading="Remove the Virtual Camera?",
            body="Other apps will no longer see the “Beamcam” camera, and it won’t come back after a restart. "
            "You can set it up again at any time.",
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("remove", "Remove")
        dialog.set_response_appearance("remove", Adw.ResponseAppearance.DESTRUCTIVE)

        def on_response(_dialog, response: str) -> None:
            if response == "remove":
                self.disconnect()
                self._run_camera_helper(remove=True)

        dialog.connect("response", on_response)
        dialog.present(self)

    # Discovery

    def _on_phones(self, phones: list[Phone]) -> bool:
        self.phones = phones
        self._render_phones()
        if self.reconnector.phones(phones) and self.reconnect_source:
            # The phone is back at a new address: no need to wait for the timer.
            GLib.source_remove(self.reconnect_source)
            self._reconnect()
        last = self.config.last_phone_id
        if self.controller is None and self.auto_connect_allowed and last and self.config.is_paired(last):
            remembered = next((p for p in phones if p.id == last), None)
            if remembered:
                self.connect_to(remembered.host, remembered.port, remembered.name, remembered.id, user=False)
        return False

    def _render_phones(self) -> None:
        while (row := self.phone_list.get_first_child()) is not None:
            self.phone_list.remove(row)
        for phone in self.phones:
            subtitle = f"Paired · {phone.host}" if self.config.is_paired(phone.id) else phone.host
            row = Adw.ActionRow(title=GLib.markup_escape_text(phone.name), subtitle=subtitle, activatable=True)
            row.add_prefix(Gtk.Image(icon_name="phone-symbolic"))
            button = Gtk.Button(label="Connect", valign=Gtk.Align.CENTER, css_classes=["suggested-action"])
            button.connect("clicked", lambda _b, p=phone: self.connect_to(p.host, p.port, p.name, p.id))
            row.add_suffix(button)
            row.set_activatable_widget(button)
            self.phone_list.append(row)
        found = bool(self.phones)
        self.phone_list.set_visible(found)
        self.searching_spinner.set_visible(not found)
        if found:
            self.phones_status.set_title("Choose Your Phone")
            self.phones_status.set_description(None)
        else:
            self.phones_status.set_title("Looking for Your Phone…")
            self.phones_status.set_description(
                "Open <b>Beamcam</b> on your Android phone.\nYour phone and this computer need to be on the same Wi-Fi."
            )

    # Connecting and streaming

    def _ask_address(self) -> None:
        dialog = Adw.AlertDialog(
            heading="Connect by Address",
            body="Type the address shown in the Beamcam app on your phone.",
        )
        entry = Gtk.Entry(placeholder_text="192.168.1.23", activates_default=True, text=self.config.last_address or "")
        dialog.set_extra_child(entry)
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("connect", "Connect")
        dialog.set_response_appearance("connect", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("connect")

        def on_response(_dialog, response: str) -> None:
            if response != "connect":
                return
            text = entry.get_text()
            try:
                host, port = parse_address(text)
            except ValueError:
                self.toast("That doesn’t look like an address. It should look like 192.168.1.23")
                return
            self.config.last_address = text.strip()
            self.config.save()
            self.connect_to(host, port, host, None)

        dialog.connect("response", on_response)
        dialog.present(self)

    def connect_to(
        self, host: str, port: int, name: str, phone_id: str | None, reconnecting: bool = False, user: bool = True
    ) -> None:
        if self.controller is not None:
            return
        self.auto_connect_allowed = False
        if not reconnecting:
            self.reconnector.connecting(Target(host, port, name, phone_id), user=user)
        self.refresh_camera_state()
        self.connecting_status.set_title(f"Reconnecting to {name}…" if reconnecting else f"Connecting to {name}…")
        self.connecting_status.set_description(None)
        self.stack.set_visible_child_name("connecting")
        device = self.camera.path if self.camera else None
        self.controller = Controller(
            host,
            port,
            computer_name(),
            device,
            self,
            self.config,
            allow_pairing=self.reconnector.pairing_allowed,
            preview=True,
        )
        self.controller.start()

    def disconnect(self) -> None:
        self.reconnector.cancel()
        if self.reconnect_source:
            GLib.source_remove(self.reconnect_source)
            self.reconnect_source = 0
            self._show_phones()
        if self.controller is not None:
            self.controller.stop()

    def _switch_camera(self) -> None:
        if self.controller is None:
            return
        self.facing = "back" if self.facing == "front" else "front"
        self.controller.set_camera(self.facing)

    # ControllerEvents (main thread)

    def on_pending(self, code: str) -> None:
        self.connecting_status.set_title("Tap “Allow” on Your Phone")
        self.connecting_status.set_description(
            f"Your phone is asking whether “{GLib.markup_escape_text(computer_name())}” may use its camera.\n"
            f"It shows the code <b>{code}</b>, the same as here."
        )

    def on_camera(self, facing: str) -> None:
        self.facing = facing

    def on_streaming(self, info: StreamInfo, pipeline: Pipeline) -> None:
        self.facing = info.facing
        self.reconnector.streaming(info.device_id or None)
        if info.device_id:
            self.config.last_phone_id = info.device_id
            self.config.save()
        self.picture.set_paintable(pipeline.paintable)
        self.streaming_title.set_label(f"Streaming from {info.device_name}")
        if self.camera:
            self.streaming_hint.set_label(f"Choose “{self.camera.label}” as the camera in your video call, browser or any other app.")
        else:
            self.streaming_hint.set_label("Preview only: set up the virtual camera to use your phone in other apps.")
        self.stack.set_visible_child_name("streaming")

    def on_ended(self, reason: str) -> None:
        self.controller = None
        self.picture.set_paintable(None)
        retry = self.reconnector.ended(reason)
        if retry is not None:
            target, delay = retry
            self.connecting_status.set_title(f"Reconnecting to {target.name}…")
            self.connecting_status.set_description("The connection dropped. Trying again…")
            self.stack.set_visible_child_name("connecting")
            self.browser.refresh()  # find the phone again in case its address changed
            self.reconnect_source = GLib.timeout_add(int(delay * 1000), self._reconnect)
            return
        self._show_phones()
        if reason != CLOSED:
            self.toast(end_message(reason))

    def _reconnect(self) -> bool:
        self.reconnect_source = 0
        target = self.reconnector.attempting()
        if target is not None:
            self.connect_to(target.host, target.port, target.name, target.phone_id, reconnecting=True)
        return False

    def _show_phones(self) -> None:
        self.stack.set_visible_child_name("phones")
        self.browser.refresh()

    # Misc

    def toast(self, text: str) -> None:
        self.toasts.add_toast(Adw.Toast(title=GLib.markup_escape_text(text), timeout=6))

    def _alert(self, heading: str, body: str) -> None:
        dialog = Adw.AlertDialog(heading=heading, body=body)
        dialog.add_response("ok", "OK")
        dialog.present(self)

    def _show_about(self) -> None:
        Adw.AboutDialog(
            application_name="Beamcam",
            application_icon=APP_ID,
            version=__version__,
            comments="Use your Android phone as a webcam.",
        ).present(self)

    def _on_close(self, _window) -> bool:
        self.browser.stop()
        if self.controller is not None:
            self.controller.stop()
        return False


def run(argv: list[str]) -> int:
    app = Adw.Application(application_id=APP_ID, flags=Gio.ApplicationFlags.DEFAULT_FLAGS)

    def on_activate(application: Adw.Application) -> None:
        window = application.get_active_window() or Window(application)
        window.present()

    app.connect("activate", on_activate)
    return app.run(argv)
