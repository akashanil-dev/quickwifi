"""Pure network selection rules. SSIDs remain bytes until displayed."""
from dataclasses import dataclass


@dataclass(frozen=True)
class Network:
    interface: str
    ssid: bytes
    security: str
    strength: int
    frequency: int
    ap_path: str
    device_path: str
    profile_path: str = ""
    connected: bool = False

    @property
    def key(self):
        return (self.interface, self.ssid, self.security)

    @property
    def name(self):
        # Never interpret a broadcast network name as markup or shell code.
        return "".join(c if c.isprintable() else "�" for c in self.ssid.decode("utf-8", "replace"))

    @property
    def band(self):
        if self.frequency >= 5925:
            return "6 GHz"
        if self.frequency >= 4900:
            return "5 GHz"
        return "2.4 GHz"


def select_networks(networks):
    """Group roaming APs, preserving adapter and security boundaries."""
    groups = {}
    for network in networks:
        if not network.ssid:
            continue
        old = groups.get(network.key)
        if old is None or (network.connected, network.strength) > (old.connected, old.strength):
            groups[network.key] = network
    return sorted(groups.values(), key=lambda n: (
        not n.connected, not bool(n.profile_path), -n.strength, n.name.casefold(), n.interface,
    ))


def password_valid(password, security):
    if security == "WPA3":
        return 1 <= len(password.encode("utf-8")) <= 63
    if security == "WPA/WPA2":
        return (8 <= len(password.encode("utf-8")) <= 63 or
                len(password) == 64 and all(c in "0123456789abcdefABCDEF" for c in password))
    return False
