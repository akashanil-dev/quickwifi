"""Use the active Omarchy palette locally, without changing the desktop theme."""
from pathlib import Path
import re
import tomllib

from gi.repository import Adw, Gdk, Gtk


def apply_theme():
    path = Path.home() / ".local/state/omarchy/current/theme/colors.toml"
    try:
        palette = tomllib.loads(path.read_text())
    except (OSError, tomllib.TOMLDecodeError):
        return None
    mode = palette.get("mode", "dark")
    Adw.StyleManager.get_default().set_color_scheme(
        Adw.ColorScheme.FORCE_LIGHT if mode == "light" else Adw.ColorScheme.FORCE_DARK)
    names = {
        "accent_bg_color": "accent", "accent_color": "accent", "accent_fg_color": "background",
        "window_bg_color": "background", "window_fg_color": "foreground",
        "view_bg_color": "background", "view_fg_color": "foreground",
        "headerbar_bg_color": "background", "headerbar_fg_color": "foreground",
        "card_bg_color": "lighter_bg", "card_fg_color": "foreground",
    }
    css = []
    for gtk_name, key in names.items():
        value = palette.get(key, "")
        if isinstance(value, str) and re.fullmatch(r"#[0-9a-fA-F]{6}", value):
            css.append(f"@define-color {gtk_name} {value};")
    css.append("""
      .quickwifi-surface { background-color: @window_bg_color; color: @window_fg_color; }
      searchentry { background-color: @card_bg_color; color: @window_fg_color; }
      searchentry text { color: @window_fg_color; background-color: transparent; }
      passwordentry { background-color: @card_bg_color; color: @window_fg_color; }
      passwordentry text { color: @window_fg_color; }
    """)
    provider = Gtk.CssProvider()
    provider.load_from_string("\n".join(css))
    Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
    return provider
