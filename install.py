#!/usr/bin/env python3
"""User-local install and narrowly scoped, reversible Omarchy integration."""
import argparse
from datetime import datetime
import json
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parent
HOME = Path.home()
APP = HOME / ".local/share/quickwifi"
PLUGIN = HOME / ".config/omarchy/plugins/akash.quickwifi"
SHELL = HOME / ".config/omarchy/shell.json"
BINDINGS = HOME / ".config/hypr/bindings.lua"
START = "-- BEGIN Quick Wi-Fi\n"
END = "-- END Quick Wi-Fi\n"
BLOCK = START + '''-- Replaces the default Network panel shortcut.
hl.unbind("SUPER + CTRL + W")
hl.unbind("SUPER + N")
o.bind("SUPER + N", "Quick Wi-Fi", "''' + str(HOME / ".local/bin/quick-wifi") + '''")
o.window("^io[.]github[.]akashanil_dev[.]quickwifi$", { float = true, center = true, size = { 480, 600 } })
''' + END


def backup(path):
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    dest = path.with_name(path.name + ".quickwifi-backup-" + stamp)
    shutil.copy2(path, dest)
    print("Backup:", dest)


def integrate(restore=False):
    if not SHELL.exists() or not BINDINGS.exists():
        raise SystemExit("Omarchy shell.json and bindings.lua are required for integration.")
    config = json.loads(SHELL.read_text())
    old, new = ("akash.quickwifi", "omarchy.network") if restore else ("omarchy.network", "akash.quickwifi")
    layout = config.get("bar", {}).get("layout", {})
    changed = False
    for entries in layout.values():
        if isinstance(entries, list):
            for entry in entries:
                if isinstance(entry, dict) and entry.get("id") == old:
                    entry["id"] = new
                    changed = True
    if not changed and not any(isinstance(e, dict) and e.get("id") == new
                               for entries in layout.values() if isinstance(entries, list) for e in entries):
        raise SystemExit(f"No {old} bar entry found; refusing to guess a layout change.")
    bindings = BINDINGS.read_text()
    if START in bindings:
        before, rest = bindings.split(START, 1)
        _, after = rest.split(END, 1)
        bindings = before + after
    if not restore:
        bindings = bindings.rstrip() + "\n\n" + BLOCK
    backup(SHELL)
    backup(BINDINGS)
    if changed:
        temporary = SHELL.with_suffix(".quickwifi-tmp")
        temporary.write_text(json.dumps(config, indent=2) + "\n")
        temporary.replace(SHELL)
    BINDINGS.write_text(bindings)
    print("Default Wi-Fi panel restored." if restore else "Quick Wi-Fi enabled in the bar and on Meta+N.")


def install():
    APP.mkdir(parents=True, exist_ok=True)
    shutil.copytree(ROOT / "quickwifi", APP / "quickwifi", dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    for name in ("quick-wifi", "README.md"):
        shutil.copy2(ROOT / name, APP / name)
    (APP / "quick-wifi").chmod(0o755)
    launcher = HOME / ".local/bin/quick-wifi"
    launcher.parent.mkdir(parents=True, exist_ok=True)
    expected = '#!/bin/sh\nexec "' + str(APP / "quick-wifi") + '" "$@"\n'
    if launcher.exists() and launcher.read_text() != expected:
        raise SystemExit(f"Refusing to overwrite unrelated launcher: {launcher}")
    launcher.write_text(expected)
    launcher.chmod(0o755)
    shutil.copytree(ROOT / "omarchy", PLUGIN, dirs_exist_ok=True)
    subprocess.run(["omarchy", "plugin", "validate", str(PLUGIN)], check=True)
    desktop = HOME / ".local/share/applications/io.github.akashanil_dev.quickwifi.desktop"
    desktop.parent.mkdir(parents=True, exist_ok=True)
    desktop.write_text('[Desktop Entry]\nType=Application\nName=Quick Wi-Fi\nComment=Connect to nearby Wi-Fi networks\n'
                       f'Exec="{launcher}"\nIcon=network-wireless\nTerminal=false\nCategories=Network;\n'
                       'StartupWMClass=io.github.akashanil_dev.quickwifi\nKeywords=wifi;wireless;network;hotspot;\n')
    print("Installed:", launcher)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--integrate", action="store_true", help="Replace the bar Wi-Fi entry and assign Meta+N, with backups")
    parser.add_argument("--restore", action="store_true", help="Restore the default bar entry and remove only our shortcut block")
    args = parser.parse_args()
    if args.restore:
        integrate(restore=True)
    else:
        install()
        if args.integrate:
            integrate()
