"""Asynchronous libnm backend: no shell commands, polling for APs, or secrets in argv."""
import getpass
import time
import uuid

import gi
gi.require_version("NM", "1.0")
from gi.repository import Gio, GLib, GObject, NM

from .model import Network, select_networks, password_valid


def security_for(ap):
    flags = int(ap.get_wpa_flags()) | int(ap.get_rsn_flags())
    # GI exports this name literally, so access it through getattr.
    sec = getattr(NM, "80211ApSecurityFlags")
    if flags & (int(sec.KEY_MGMT_802_1X) | int(sec.KEY_MGMT_EAP_SUITE_B_192)):
        return "Enterprise"
    # Prefer PSK for a transition AP so existing WPA2 profiles remain usable.
    if flags & int(sec.KEY_MGMT_PSK):
        return "WPA/WPA2"
    if flags & int(sec.KEY_MGMT_SAE):
        return "WPA3"
    if flags & (int(sec.KEY_MGMT_OWE) | int(sec.KEY_MGMT_OWE_TM)):
        return "Enhanced open"
    if flags or int(ap.get_flags()) & 1:
        return "Legacy / other"
    return "Open"


def make_connection(network, password=None):
    connection = NM.SimpleConnection.new()
    general = NM.SettingConnection.new()
    general.props.id = network.name
    general.props.uuid = str(uuid.uuid4())
    general.props.type = "802-11-wireless"
    general.props.autoconnect = True
    general.add_permission("user", getpass.getuser(), None)
    connection.add_setting(general)
    wifi = NM.SettingWireless.new()
    wifi.props.ssid = GLib.Bytes.new(network.ssid)
    wifi.props.mode = "infrastructure"
    connection.add_setting(wifi)
    if network.security != "Open":
        if network.security not in ("WPA/WPA2", "WPA3", "Enhanced open"):
            raise ValueError("Configure this network in Advanced settings first.")
        sec = NM.SettingWirelessSecurity.new()
        sec.props.key_mgmt = {"WPA/WPA2": "wpa-psk", "WPA3": "sae", "Enhanced open": "owe"}[network.security]
        if network.security != "Enhanced open":
            if not password_valid(password or "", network.security):
                raise ValueError("Enter a valid Wi-Fi password.")
            sec.props.psk = password
        connection.add_setting(sec)
    for klass in (NM.SettingIP4Config, NM.SettingIP6Config):
        setting = klass.new()
        setting.props.method = "auto"
        connection.add_setting(setting)
    connection.normalize()
    connection.verify()
    return connection


class Backend(GObject.Object):
    __gsignals__ = {
        "changed": (GObject.SignalFlags.RUN_FIRST, None, ()),
        "message": (GObject.SignalFlags.RUN_FIRST, None, (str, bool)),
    }

    def __init__(self):
        super().__init__()
        self.client = None
        self.networks = []
        self.ready = False
        self.enabled = False
        self.hardware_enabled = True
        self.adapters = []
        self.scans = {}
        self.scan_generation = 0
        self.last_scan_time = None
        self.action = None
        self.failed_key = None
        self.watches = []
        self.refresh_id = 0
        self.started_at = time.monotonic()
        self.first_results_ms = None
        self.cancellable = Gio.Cancellable()
        NM.Client.new_async(self.cancellable, self._client_ready, None)

    @property
    def scanning(self):
        return bool(self.scans)

    def _client_ready(self, source, result, *_):
        try:
            self.client = NM.Client.new_finish(result)
            for signal in ("device-added", "device-removed", "connection-added", "connection-removed",
                           "notify::wireless-enabled", "notify::wireless-hardware-enabled", "notify::nm-running"):
                self.client.connect(signal, self.queue_refresh)
            self.refresh()
        except GLib.Error as error:
            self.emit("message", f"Cannot reach NetworkManager: {error.message}", True)

    def queue_refresh(self, *_):
        if not self.refresh_id:
            # Coalesce a D-Bus event burst, without a user-visible polling delay.
            self.refresh_id = GLib.idle_add(self.refresh)

    def _watch(self, obj, signal, callback=None):
        self.watches.append((obj, obj.connect(signal, callback or self.queue_refresh)))

    def refresh(self):
        self.refresh_id = 0
        if not self.client:
            return GLib.SOURCE_REMOVE
        for obj, handler in self.watches:
            # NM.Device.disconnect is a network operation, not GObject's signal API.
            GObject.signal_handler_disconnect(obj, handler)
        self.watches.clear()
        self.ready = self.client.get_nm_running()
        self.enabled = self.client.wireless_get_enabled()
        self.hardware_enabled = self.client.wireless_hardware_get_enabled()
        devices = [d for d in self.client.get_devices() if isinstance(d, NM.DeviceWifi)]
        self.adapters = devices
        paths = {d.get_path() for d in devices}
        self.scans = {p: s for p, s in self.scans.items() if p in paths}
        if not self.enabled or not self.ready:
            self.scans.clear()
        networks = []
        for device in devices:
            for signal in ("access-point-added", "access-point-removed", "notify::active-access-point",
                           "notify::state", "notify::available-connections", "notify::last-scan"):
                self._watch(device, signal)
            pending = self.scans.get(device.get_path())
            if pending and device.get_last_scan() > pending[0]:
                self.scans.pop(device.get_path(), None)
                self.last_scan_time = time.monotonic()
            active_ap = device.get_active_access_point()
            active_path = active_ap.get_path() if active_ap else ""
            profiles = list(device.get_available_connections())
            profiles.sort(key=lambda c: c.get_setting_connection().get_timestamp(), reverse=True)
            for ap in device.get_access_points():
                for signal in ("notify::strength", "notify::ssid", "notify::flags", "notify::wpa-flags", "notify::rsn-flags"):
                    self._watch(ap, signal)
                raw_ssid = ap.get_ssid()
                if raw_ssid is None:
                    continue
                profile = next((p for p in profiles if ap.connection_valid(p) and device.connection_valid(p)), None)
                networks.append(Network(
                    device.get_iface(), bytes(raw_ssid.get_data()), security_for(ap),
                    ap.get_strength(), ap.get_frequency(), ap.get_path(), device.get_path(),
                    profile.get_path() if profile else "",
                    ap.get_path() == active_path and device.get_state() == NM.DeviceState.ACTIVATED,
                ))
        self.networks = select_networks(networks) if self.enabled and self.ready else []
        if self.first_results_ms is None and self.ready:
            self.first_results_ms = (time.monotonic() - self.started_at) * 1000
        self._check_action()
        self.emit("changed")
        return GLib.SOURCE_REMOVE

    def scan(self):
        if not self.ready or not self.enabled or self.scanning or self.action:
            return
        self.scan_generation += 1
        token = self.scan_generation
        for device in self.adapters:
            if device.get_state() in (NM.DeviceState.UNMANAGED, NM.DeviceState.UNAVAILABLE):
                continue
            path = device.get_path()
            self.scans[path] = (device.get_last_scan(), token)
            device.request_scan_async(self.cancellable, self._scan_requested, (path, token))
        self.emit("changed")
        GLib.timeout_add_seconds(15, self._scan_timeout, token)

    def _scan_requested(self, device, result, data):
        path, token = data
        try:
            device.request_scan_finish(result)
            # Request accepted is not scan completed: LastScan drives completion.
            self.queue_refresh()
        except GLib.Error as error:
            if self.scans.get(path, (None, None))[1] == token:
                self.scans.pop(path, None)
                self.emit("message", f"Scan deferred by NetworkManager: {error.message}", False)
                self.emit("changed")

    def _scan_timeout(self, token):
        expired = [p for p, s in self.scans.items() if s[1] == token]
        for path in expired:
            self.scans.pop(path)
        if expired:
            self.emit("message", "Scan has not completed. Existing networks are still available; try Refresh.", False)
            self.emit("changed")
        return GLib.SOURCE_REMOVE

    def resolve(self, network):
        device = next((d for d in self.adapters if d.get_path() == network.device_path), None)
        if device is None:
            raise ValueError("This Wi-Fi adapter was removed.")
        ap = next((a for a in device.get_access_points() if a.get_path() == network.ap_path), None)
        if ap is None or ap.get_ssid() is None or bytes(ap.get_ssid().get_data()) != network.ssid or security_for(ap) != network.security:
            raise ValueError("This network changed or is no longer nearby. Refresh and try again.")
        return device, ap

    def connect_network(self, network, password=None):
        if self.action or not self.ready or not self.enabled:
            return
        try:
            device, ap = self.resolve(network)
            if network.connected:
                return
            profile = next((c for c in device.get_available_connections()
                            if c.get_path() == network.profile_path and ap.connection_valid(c)), None)
            self.action = {"key": network.key, "device": device, "name": network.name,
                           "active": None, "started": time.monotonic(), "kind": "connect"}
            self.failed_key = None
            self.scans.clear()
            if profile and password is None:
                self._activate(profile, device, ap)
            elif profile and password is not None:
                # Preserve the saved profile's DNS, routing, security policy, etc.
                update = NM.SimpleConnection.new_clone(profile)
                security = update.get_setting_wireless_security()
                if not security or security.get_key_mgmt() not in ("wpa-psk", "sae"):
                    raise ValueError("Edit this profile in Advanced settings.")
                if not password_valid(password, network.security):
                    raise ValueError("Enter a valid Wi-Fi password.")
                security.props.psk = password
                security.props.psk_flags = NM.SettingSecretFlags.NONE
                profile.update2(update.to_dbus(NM.ConnectionSerialization.ALL), NM.SettingsUpdate2Flags.TO_DISK,
                                None, self.cancellable, self._profile_updated, (device, ap, self.action))
            else:
                connection = make_connection(network, password)
                self.client.add_and_activate_connection_async(connection, device, ap.get_path(),
                                                             self.cancellable, self._activated, (True, self.action))
            self.emit("changed")
            GLib.timeout_add_seconds(45, self._action_slow, self.action)
        except (GLib.Error, ValueError) as error:
            self.action = None
            self.emit("message", str(error), True)
            self.emit("changed")

    def _activate(self, profile, device, ap):
        self.client.activate_connection_async(profile, device, ap.get_path(), self.cancellable,
                                              self._activated, (False, self.action))

    def _profile_updated(self, profile, result, data):
        device, ap, action = data
        try:
            profile.update2_finish(result)
            if self.action is action:
                self._activate(profile, device, ap)
        except GLib.Error as error:
            self._action_error(error.message, action)

    def _activated(self, client, result, data):
        added, action = data
        try:
            active = (client.add_and_activate_connection_finish(result) if added
                      else client.activate_connection_finish(result))
            if self.action is action:
                action["active"] = active
                action["handler"] = active.connect("notify::state", self.queue_refresh)
                self.queue_refresh()
        except GLib.Error as error:
            self._action_error(error.message, action)

    def _clear_action(self):
        action = self.action
        if action and action.get("handler"):
            GObject.signal_handler_disconnect(action["active"], action["handler"])
        self.action = None

    def _action_error(self, message, action):
        if self.action is not action:
            return
        self.failed_key = action["key"]
        self._clear_action()
        self.emit("message", f"Could not connect: {message}", True)
        self.emit("changed")

    def _check_action(self):
        action = self.action
        if not action or not action.get("active"):
            return
        active = action["active"]
        if active.get_state() == NM.ActiveConnectionState.ACTIVATED:
            elapsed = time.monotonic() - action["started"]
            self._clear_action()
            self.emit("message", f"Connected to {action['name']} in {elapsed:.1f}s.", False)
        elif active.get_state() == NM.ActiveConnectionState.DEACTIVATED:
            reason = active.get_state_reason().value_nick.replace("-", " ")
            self._action_error(reason + ". Check the password or open Advanced settings.", action)

    def _action_slow(self, action):
        if self.action is action:
            self.emit("message", "NetworkManager is still connecting. You can cancel this attempt.", False)
        return GLib.SOURCE_REMOVE

    def disconnect_network(self, network):
        if self.action:
            return
        try:
            device, ap = self.resolve(network)
            active_ap = device.get_active_access_point()
            if not active_ap or active_ap.get_path() != ap.get_path():
                raise ValueError("The active network changed. Refresh before disconnecting.")
            device.disconnect_async(self.cancellable, self._disconnected, None)
        except (GLib.Error, ValueError) as error:
            self.emit("message", str(error), True)

    def _disconnected(self, device, result, *_):
        try:
            device.disconnect_finish(result)
        except GLib.Error as error:
            self.emit("message", error.message, True)
        self.queue_refresh()

    def cancel_connection(self):
        if not self.action:
            return
        active = self.action.get("active")
        if not active:
            self.emit("message", "Waiting for NetworkManager to accept the request; try Cancel again shortly.", False)
            return
        action = self.action
        self.client.deactivate_connection_async(active, self.cancellable, self._cancelled, action)

    def _cancelled(self, client, result, action):
        try:
            client.deactivate_connection_finish(result)
            if self.action is action:
                self._clear_action()
                self.emit("message", "Connection cancelled.", False)
            self.queue_refresh()
        except GLib.Error as error:
            self.emit("message", error.message, True)

    def set_enabled(self, enabled):
        if not self.client:
            return
        self.client.dbus_set_property("/org/freedesktop/NetworkManager", "org.freedesktop.NetworkManager",
                                      "WirelessEnabled", GLib.Variant("b", enabled), 10000,
                                      self.cancellable, self._radio_changed, None)

    def _radio_changed(self, client, result, *_):
        try:
            client.dbus_set_property_finish(result)
        except GLib.Error as error:
            self.emit("message", error.message, True)
        self.queue_refresh()
