"""Real GTK widgets with a fake network backend. No system networking calls."""
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from quickwifi.app import Adw, Gio, GLib, Gtk, WifiWindow
from quickwifi.backend import GObject
from quickwifi.model import Network
from quickwifi.theme import apply_theme


class FakeBackend(GObject.Object):
    __gsignals__ = {
        "changed": (GObject.SignalFlags.RUN_FIRST, None, ()),
        "message": (GObject.SignalFlags.RUN_FIRST, None, (str, bool)),
    }

    def __init__(self):
        super().__init__()
        import time
        self.started_at = time.monotonic()
        self.ready = self.enabled = self.hardware_enabled = True
        self.adapters = [object()]
        self.scanning = False
        self.action = self.last_scan_time = self.failed_key = None
        self.calls = []
        self.networks = [
            Network("wlan0", b"Home", "WPA/WPA2", 75, 5180, "/ap/1", "/device/1", "/profile/1", True),
            Network("wlan0", b"Phone hotspot", "WPA/WPA2", 90, 2412, "/ap/2", "/device/1"),
            Network("wlan0", b"Office", "WPA/WPA2", 60, 5180, "/ap/3", "/device/1", "/profile/3"),
        ]

    def scan(self): self.calls.append(("scan",))
    def set_enabled(self, enabled): self.calls.append(("radio", enabled))
    def connect_network(self, network, password=None): self.calls.append(("connect", network.key, password))
    def disconnect_network(self, network): self.calls.append(("disconnect", network.key))
    def cancel_connection(self): self.calls.append(("cancel",))


class UiTest(Adw.Application):
    def __init__(self):
        super().__init__(application_id="io.github.akashanil_dev.quickwifi", flags=Gio.ApplicationFlags.NON_UNIQUE)
        self.result = {"passed": False}

    def do_activate(self):
        self.provider = apply_theme()
        self.backend = FakeBackend()
        self.window = WifiWindow(self, self.backend)
        self.window.set_title("Quick Wi-Fi · Test preview")
        self.window.present()
        GLib.timeout_add(500, self.exercise)

    def exercise(self):
        try:
            w, b = self.window, self.backend
            if "--snapshot" in sys.argv:
                snapshot = Gtk.Snapshot.new()
                widget = w.get_content()
                Gtk.WidgetPaintable.new(widget).snapshot(snapshot, widget.get_width(), widget.get_height())
                texture = w.get_renderer().render_texture(snapshot.to_node(), None)
                texture.save_to_png(str(Path(__file__).resolve().parents[1] / "docs/preview.png"))
            assert len(w.rows) == 3
            w.search.set_text("Office")
            w.apply_filter()
            w.activate_first()
            assert b.calls[-1] == ("connect", b.networks[2].key, None)
            w.search.set_text("Home")
            w.apply_filter()
            count = len(b.calls)
            w.activate_first()
            assert len(b.calls) == count, "Enter must never disconnect the active network"
            w.search.set_text("")
            w.apply_filter()
            old_row = w.rows[b.networks[1].key]
            w.choose(b.networks[1])
            dialog = w.password_dialog
            assert dialog is not None
            entry = dialog.get_extra_child()
            entry.set_text("short")
            assert not dialog.get_response_enabled("connect")
            entry.set_text("sample-test-password")
            assert dialog.get_response_enabled("connect")
            b.emit("changed")
            assert entry.get_text() == "sample-test-password"
            assert w.rows[b.networks[1].key] is old_row, "Scan updates should preserve row widgets"
            dialog.emit("response", "connect")
            assert b.calls[-1] == ("connect", b.networks[1].key, "sample-test-password")
            assert entry.get_text() == ""
            assert w.password_dialog is None
            dialog.close()
            b.action = {"key": b.networks[1].key, "name": "Phone hotspot"}
            b.emit("changed")
            assert all(not row.button.get_sensitive() for row in w.rows.values())
            assert w.cancel.get_visible()
            w.cancel.emit("clicked")
            assert b.calls[-1] == ("cancel",)
            b.action = None
            b.failed_key = b.networks[2].key
            b.emit("changed")
            w.choose(b.networks[2])
            assert w.password_dialog is not None, "Saved-password failure must offer retry"
            w.password_dialog.close()
            self.result = {
                "passed": True,
                "checks": ["saved-profile one-click", "search Enter never disconnects", "password validation",
                           "password prompt survives AP updates", "stable row widgets", "password cleared after submit",
                           "busy controls", "cancel dispatch", "saved-password retry"],
            }
        except Exception as error:
            import traceback
            traceback.print_exc()
            self.result = {"passed": False, "error": str(error)}
        finally:
            print(json.dumps(self.result, indent=2), flush=True)
            artifacts = Path(__file__).resolve().parents[1] / "artifacts"
            artifacts.mkdir(exist_ok=True)
            (artifacts / "ui-smoke.json").write_text(json.dumps(self.result, indent=2) + "\n")
            self.quit()
        return GLib.SOURCE_REMOVE


app = UiTest()
app.run([sys.argv[0]])
raise SystemExit(0 if app.result["passed"] else 1)
