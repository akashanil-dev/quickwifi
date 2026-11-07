"""Small GTK4 frontend; libnm handles all networking asynchronously."""
import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys
import time

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk, Pango

from .backend import Backend
from .model import password_valid
from .theme import apply_theme


class NetworkRow(Adw.ActionRow):
    def __init__(self, window, network):
        super().__init__()
        self.window = window
        self.network = network
        self.set_use_markup(False)
        self.set_title_lines(1)
        self.set_subtitle_lines(1)
        self.icon = Gtk.Image(pixel_size=22)
        self.add_prefix(self.icon)
        self.button = Gtk.Button(valign=Gtk.Align.CENTER)
        self.button.connect("clicked", lambda *_: window.choose(self.network))
        self.add_suffix(self.button)
        self.set_activatable_widget(self.button)
        self.update(network)

    def update(self, network):
        self.network = network
        backend = self.window.backend
        self.set_title(network.name)
        quality = "excellent" if network.strength >= 80 else "good" if network.strength >= 55 else "ok" if network.strength >= 30 else "weak"
        self.icon.set_from_icon_name(f"network-wireless-signal-{quality}-symbolic")
        state = "Connected" if network.connected else "Saved" if network.profile_path else network.security
        detail = f"{state} · {network.band} · {network.strength}%"
        if len(backend.adapters) > 1:
            detail += f" · {network.interface}"
        self.set_subtitle(detail)
        busy = backend.action is not None
        ours = busy and backend.action["key"] == network.key
        self.button.set_label("Connecting…" if ours else "Disconnect" if network.connected else "Connect")
        self.button.set_sensitive(not busy)
        if network.connected:
            self.icon.add_css_class("success")
            self.button.remove_css_class("suggested-action")
        else:
            self.icon.remove_css_class("success")
            self.button.add_css_class("suggested-action")


class WifiWindow(Adw.ApplicationWindow):
    def __init__(self, app, backend):
        super().__init__(application=app, title="Quick Wi-Fi", default_width=480, default_height=620)
        self.backend = backend
        self.rows = {}
        self.password_dialog = None
        self.pending_scan = True
        self.previous_enabled = False
        self.first_render_ms = None
        self.message_text = ""
        self.message_error = False
        self.set_size_request(360, 420)
        toolbar = Adw.ToolbarView()
        toolbar.add_css_class("quickwifi-surface")
        self.set_content(toolbar)
        header = Adw.HeaderBar()
        header.set_title_widget(Adw.WindowTitle(title="Quick Wi-Fi", subtitle="Nearby networks, ready to connect"))
        toolbar.add_top_bar(header)
        self.scan_button = Gtk.Button(icon_name="view-refresh-symbolic", tooltip_text="Refresh networks (Ctrl+R)")
        self.scan_button.connect("clicked", self.request_scan)
        header.pack_start(self.scan_button)
        self.radio = Gtk.Switch(valign=Gtk.Align.CENTER, tooltip_text="Wi-Fi")
        self.radio.connect("state-set", self.change_radio)
        header.pack_end(self.radio)

        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16,
                       margin_top=12, margin_bottom=16, margin_start=20, margin_end=20)
        toolbar.set_content(body)
        self.search = Gtk.SearchEntry(placeholder_text="Find a network…", hexpand=True)
        self.search.set_search_delay(0)
        self.search.connect("search-changed", lambda *_: self.apply_filter())
        self.search.connect("activate", self.activate_first)
        body.append(self.search)
        status_box = Gtk.Box(spacing=8)
        self.spinner = Gtk.Spinner()
        status_box.append(self.spinner)
        self.status = Gtk.Label(label="Loading Wi-Fi…", xalign=0, hexpand=True, ellipsize=Pango.EllipsizeMode.END)
        self.status.add_css_class("dim-label")
        status_box.append(self.status)
        self.cancel = Gtk.Button(label="Cancel", visible=False)
        self.cancel.connect("clicked", lambda *_: backend.cancel_connection())
        status_box.append(self.cancel)
        body.append(status_box)

        self.notice = Gtk.Label(xalign=0, wrap=True, visible=False)
        self.notice.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        self.notice.add_css_class("caption")
        body.append(self.notice)
        self.scroll = Gtk.ScrolledWindow(vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
        body.append(self.scroll)
        self.list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        self.list.add_css_class("boxed-list")
        self.scroll.set_child(self.list)
        self.empty = Adw.StatusPage(title="Looking for networks", icon_name="network-wireless-symbolic")
        self.empty.add_css_class("compact")
        self.list.set_placeholder(self.empty)
        self.list.set_sort_func(self.sort_rows)

        footer = Gtk.Box(spacing=12)
        advanced = Gtk.Button(label="Advanced settings", tooltip_text="Edit saved profiles, enterprise Wi-Fi, or hidden networks")
        advanced.add_css_class("flat")
        advanced.connect("clicked", lambda *_: self.open_settings())
        footer.append(advanced)
        help_label = Gtk.Label(label="Enter to connect · Esc to close", hexpand=True, xalign=1)
        help_label.add_css_class("dim-label")
        help_label.add_css_class("caption")
        footer.append(help_label)
        body.append(footer)

        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self.key_pressed)
        self.add_controller(keys)
        backend.connect("changed", lambda *_: self.render())
        backend.connect("message", self.show_message)
        self.connect("close-request", self.close_requested)
        self.connect("notify::is-active", self.window_active)
        GLib.timeout_add_seconds(15, self.auto_scan)
        self.render()

    def sort_rows(self, a, b):
        def key(row):
            n = row.network
            return (not n.connected, not bool(n.profile_path), -n.strength, n.name.casefold(), n.interface)
        return (key(a) > key(b)) - (key(a) < key(b))

    def show_message(self, backend, message, error):
        self.message_text, self.message_error = message, error
        self.notice.set_label(message)
        self.notice.set_visible(bool(message))
        if error:
            self.notice.add_css_class("error")
        else:
            self.notice.remove_css_class("error")

    def render(self):
        backend = self.backend
        self.radio.set_state(backend.enabled)
        self.radio.set_active(backend.enabled)
        self.radio.set_sensitive(backend.ready and backend.hardware_enabled and not backend.action)
        self.scan_button.set_sensitive(backend.ready and backend.enabled and not backend.scanning and not backend.action)
        busy = bool(backend.scanning or backend.action)
        self.spinner.set_spinning(busy)
        self.spinner.set_visible(busy)
        self.cancel.set_visible(backend.action is not None)
        if backend.action:
            self.status.set_label(f"Connecting to {backend.action['name']}…")
        elif not backend.ready:
            self.status.set_label("Waiting for NetworkManager")
        elif not backend.hardware_enabled:
            self.status.set_label("Wi-Fi is blocked by the hardware switch")
        elif not backend.enabled:
            self.status.set_label("Wi-Fi is turned off")
        elif not backend.adapters:
            self.status.set_label("No Wi-Fi adapter found")
        elif backend.scanning:
            self.status.set_label("Looking for new networks…")
        else:
            count = len(backend.networks)
            label = f"{count} nearby network{'s' if count != 1 else ''}"
            if backend.last_scan_time:
                label += " · Scan complete"
            self.status.set_label(label)
        current = {n.key: n for n in backend.networks}
        for key in list(self.rows):
            if key not in current:
                self.list.remove(self.rows.pop(key))
        for key, network in current.items():
            if key in self.rows:
                self.rows[key].update(network)
            else:
                self.rows[key] = NetworkRow(self, network)
                self.list.append(self.rows[key])
        self.list.invalidate_sort()
        self.apply_filter()
        if backend.ready and self.first_render_ms is None:
            self.first_render_ms = (time.monotonic() - backend.started_at) * 1000
        if backend.enabled and (self.pending_scan or not self.previous_enabled):
            self.pending_scan = False
            # Paint cached APs before issuing an asynchronous request.
            GLib.idle_add(self.scan_once)
        self.previous_enabled = backend.enabled

    def apply_filter(self):
        query = self.search.get_text().casefold()
        for row in self.rows.values():
            row.set_visible(query in row.network.name.casefold())
        if query:
            self.empty.set_title("No matching networks")
            self.empty.set_description("Try a different network name.")
        elif not self.backend.enabled and self.backend.ready:
            self.empty.set_title("Wi-Fi is off")
            self.empty.set_description("Use the switch above to turn it on.")
        elif not self.backend.adapters and self.backend.ready:
            self.empty.set_title("No Wi-Fi adapter")
            self.empty.set_description("Connect an adapter managed by NetworkManager.")
        else:
            self.empty.set_title("Looking for networks" if self.backend.scanning else "No nearby networks")
            self.empty.set_description("Turn on your hotspot, then press Refresh.")

    def change_radio(self, switch, enabled):
        if enabled != self.backend.enabled:
            self.backend.set_enabled(enabled)
        return True

    def request_scan(self, *_):
        self.show_message(self.backend, "", False)
        self.backend.scan()

    def scan_once(self):
        if self.get_visible():
            self.backend.scan()
        return GLib.SOURCE_REMOVE

    def auto_scan(self):
        if self.get_visible() and self.is_active() and not self.password_dialog:
            self.backend.scan()
        return GLib.SOURCE_CONTINUE

    def window_active(self, *_):
        if self.is_active() and self.backend.ready and not self.password_dialog:
            # Reopening/refocusing asks NM now, not after an extra frontend gate.
            self.scan_once()

    def choose(self, network):
        if self.backend.action:
            return
        self.show_message(self.backend, "", False)
        if network.connected:
            self.backend.disconnect_network(network)
        elif network.security in ("WPA/WPA2", "WPA3") and (
                not network.profile_path or self.backend.failed_key == network.key):
            self.ask_password(network)
        elif not network.profile_path and network.security in ("Enterprise", "Legacy / other"):
            self.show_message(self.backend, "Configure this network's authentication in Advanced settings, then connect here.", False)
            self.open_settings()
        else:
            self.backend.connect_network(network)

    def ask_password(self, network):
        if self.password_dialog:
            return
        dialog = Adw.AlertDialog(heading=f"Connect to {network.name}", body="Enter the Wi-Fi password. NetworkManager will save it for your account.")
        entry = Gtk.PasswordEntry(show_peek_icon=True, activates_default=True)
        entry.set_property("placeholder-text", "Wi-Fi password")
        dialog.set_extra_child(entry)
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("connect", "Connect")
        dialog.set_response_appearance("connect", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_response_enabled("connect", False)
        dialog.set_default_response("connect")
        dialog.set_close_response("cancel")
        entry.connect("changed", lambda *_: dialog.set_response_enabled("connect", password_valid(entry.get_text(), network.security)))

        def respond(_, response):
            password = entry.get_text()
            entry.set_text("")
            self.password_dialog = None
            if response == "connect":
                self.backend.connect_network(network, password)
            password = None

        dialog.connect("response", respond)
        self.password_dialog = dialog
        dialog.present(self)
        entry.grab_focus()
        return entry

    def open_settings(self):
        executable = shutil.which("nm-connection-editor")
        if executable:
            subprocess.Popen([executable], start_new_session=True)
        else:
            self.show_message(self.backend, "Install nm-connection-editor to configure advanced connections.", True)

    def activate_first(self, *_):
        # Enter in search should connect, never disconnect the current network.
        rows = sorted((r for r in self.rows.values() if r.get_visible() and not r.network.connected),
                      key=lambda r: (not bool(r.network.profile_path), -r.network.strength, r.network.name.casefold()))
        if rows:
            self.choose(rows[0].network)

    def key_pressed(self, controller, keyval, keycode, state):
        if keyval == Gdk.KEY_Escape:
            if self.password_dialog:
                self.password_dialog.close()
            else:
                self.close()
            return True
        if state & Gdk.ModifierType.CONTROL_MASK:
            if keyval in (Gdk.KEY_r, Gdk.KEY_R):
                self.request_scan()
                return True
            if keyval in (Gdk.KEY_f, Gdk.KEY_F):
                self.search.grab_focus()
                return True
        return False

    def close_requested(self, *_):
        # Keep callbacks alive during an explicit connection attempt. A closed
        # window never scans; it can be reopened through the single-instance app.
        if self.backend.action:
            self.set_visible(False)
            return True
        return False


class WifiApp(Adw.Application):
    def __init__(self, args):
        super().__init__(application_id="io.github.akashanil_dev.quickwifi", flags=Gio.ApplicationFlags.DEFAULT_FLAGS)
        self.args = args
        self.window = None
        self.backend = None

    def do_activate(self):
        if self.window is None:
            self.theme_provider = apply_theme()
            self.backend = Backend()
            self.window = WifiWindow(self, self.backend)
        self.window.present()
        self.window.search.grab_focus()
        if self.args.smoke_test:
            GLib.timeout_add_seconds(8, self.smoke_test)

    def smoke_test(self):
        backend, window = self.backend, self.window
        results = {
            "nm_running": backend.ready,
            "adapters": len(backend.adapters),
            "networks": len(backend.networks),
            "cached_results_ms": round(backend.first_results_ms or 0, 1),
            "first_render_ms": round(window.first_render_ms or 0, 1),
            "scan_completed": backend.last_scan_time is not None,
        }
        if self.args.snapshot:
            try:
                paintable = Gtk.WidgetPaintable.new(window.get_content())
                snapshot = Gtk.Snapshot.new()
                paintable.snapshot(snapshot, window.get_content().get_width(), window.get_content().get_height())
                node = snapshot.to_node()
                if node is None:
                    raise RuntimeError("GTK did not provide a render node")
                texture = window.get_renderer().render_texture(node, None)
                texture.save_to_png(self.args.snapshot)
                results["snapshot"] = True
            except Exception as error:
                results["snapshot_error"] = str(error)
        # Exercise search without initiating any connection or changing radio state.
        window.search.set_text("__quickwifi_no_match__")
        window.apply_filter()
        results["filter_hides_nonmatches"] = all(not r.get_visible() for r in window.rows.values())
        window.search.set_text("")
        window.apply_filter()
        results["filter_restores_rows"] = all(r.get_visible() for r in window.rows.values())
        print(json.dumps(results, indent=2), flush=True)
        if self.args.report:
            Path(self.args.report).write_text(json.dumps(results, indent=2) + "\n")
        self.quit()
        return GLib.SOURCE_REMOVE


def main():
    parser = argparse.ArgumentParser(description="Fast Wi-Fi access for Omarchy and other NetworkManager desktops")
    parser.add_argument("--smoke-test", action="store_true", help="Read live networks, request one scan, exercise search, and exit. Never connects.")
    parser.add_argument("--report", help="Write a smoke-test JSON report without SSIDs or credentials")
    parser.add_argument("--snapshot", help="Export the app window to PNG during a smoke test")
    args = parser.parse_args()
    return WifiApp(args).run([sys.argv[0]])
