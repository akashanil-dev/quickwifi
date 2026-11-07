import unittest
from dataclasses import replace

from quickwifi.model import Network, select_networks, password_valid
from quickwifi.backend import make_connection, security_for


class ModelTests(unittest.TestCase):
    def setUp(self):
        self.network = Network("wlan0", b"example", "WPA/WPA2", 80, 5180, "/ap/1", "/device/1")

    def test_same_name_different_security_or_adapter_remain_distinct(self):
        networks = [self.network, replace(self.network, security="Open"), replace(self.network, interface="wlan1")]
        self.assertEqual(len(select_networks(networks)), 3)

    def test_active_ap_wins_over_stronger_roaming_ap(self):
        active = replace(self.network, strength=20, connected=True)
        self.assertEqual(select_networks([active, self.network]), [active])

    def test_prefers_connected_then_saved_then_strength(self):
        active = replace(self.network, ssid=b"active", connected=True, strength=10)
        saved = replace(self.network, ssid=b"saved", profile_path="/profile/1", strength=20)
        self.assertEqual(select_networks([self.network, saved, active]), [active, saved, self.network])

    def test_raw_ssids_do_not_collapse_after_decoding(self):
        a, b = replace(self.network, ssid=b"bad\xff"), replace(self.network, ssid=b"bad\xfe")
        self.assertEqual(a.name, b.name)
        self.assertEqual(len(select_networks([a, b])), 2)

    def test_hidden_and_control_characters(self):
        self.assertEqual(select_networks([replace(self.network, ssid=b"")]), [])
        self.assertEqual(replace(self.network, ssid=b"a\nb").name, "a�b")

    def test_password_boundaries_and_hex(self):
        self.assertFalse(password_valid("short", "WPA/WPA2"))
        self.assertTrue(password_valid("a" * 8, "WPA/WPA2"))
        self.assertTrue(password_valid("a" * 63, "WPA/WPA2"))
        self.assertTrue(password_valid("f" * 64, "WPA/WPA2"))
        self.assertFalse(password_valid("g" * 64, "WPA/WPA2"))
        self.assertFalse(password_valid("é" * 32, "WPA/WPA2"))
        self.assertTrue(password_valid("a", "WPA3"))

    def test_new_profiles_pass_real_libnm_validation(self):
        for security in ("Open", "WPA/WPA2", "WPA3", "Enhanced open"):
            with self.subTest(security=security):
                connection = make_connection(replace(self.network, security=security), "samplepassword")
                self.assertTrue(connection.verify())
                self.assertEqual(bytes(connection.get_setting_wireless().get_ssid().get_data()), self.network.ssid)
                self.assertEqual(connection.get_setting_connection().get_num_permissions(), 1)
                self.assertEqual(connection.get_setting_ip4_config().get_method(), "auto")

    def test_open_profile_has_no_security_and_owe_has_no_password(self):
        open_profile = make_connection(replace(self.network, security="Open"))
        self.assertIsNone(open_profile.get_setting_wireless_security())
        owe = make_connection(replace(self.network, security="Enhanced open"))
        self.assertEqual(owe.get_setting_wireless_security().get_key_mgmt(), "owe")
        self.assertIsNone(owe.get_setting_wireless_security().get_psk())

    def test_enterprise_requires_explicit_configuration(self):
        with self.assertRaises(ValueError):
            make_connection(replace(self.network, security="Enterprise"), "samplepassword")

    def test_security_classification_never_treats_unknown_encryption_as_open(self):
        class AP:
            def __init__(self, flags, security): self.flags, self.security = flags, security
            def get_flags(self): return self.flags
            def get_wpa_flags(self): return 0
            def get_rsn_flags(self): return self.security
        for flags, security, label in [(0, 0, "Open"), (1, 0, "Legacy / other"),
                                       (1, 256, "WPA/WPA2"), (1, 1024, "WPA3"),
                                       (1, 1280, "WPA/WPA2"), (1, 2048, "Enhanced open"),
                                       (1, 512, "Enterprise"), (0, 16384, "Legacy / other")]:
            self.assertEqual(security_for(AP(flags, security)), label)


if __name__ == "__main__":
    unittest.main()
