import time
import unittest
from unittest.mock import MagicMock, patch

from gi.repository import GLib
from quickwifi.backend import Backend, NM
from quickwifi.model import Network


class BackendTests(unittest.TestCase):
    def setUp(self):
        with patch("quickwifi.backend.NM.Client.new_async"):
            self.backend = Backend()
        self.backend.client = MagicMock()
        self.backend.ready = self.backend.enabled = True
        self.messages = []
        self.backend.connect("message", lambda _, message, error: self.messages.append((message, error)))
        self.device = MagicMock()
        self.device.get_path.return_value = "/device/1"
        self.device.get_state.return_value = NM.DeviceState.ACTIVATED
        self.device.get_last_scan.return_value = 10
        self.backend.adapters = [self.device]
        self.network = Network("wlan0", b"example", "Open", 80, 5180, "/ap/1", "/device/1")

    def tearDown(self):
        if self.backend.refresh_id:
            GLib.source_remove(self.backend.refresh_id)

    def test_scan_does_not_clear_existing_results(self):
        self.backend.networks = [self.network]
        with patch("quickwifi.backend.GLib.timeout_add_seconds"):
            self.backend.scan()
            self.backend.scan()
        self.assertEqual(self.backend.networks, [self.network])
        self.device.request_scan_async.assert_called_once()
        self.assertTrue(self.backend.scanning)

    def test_request_accepted_is_not_scan_complete(self):
        self.backend.scans = {"/device/1": (10, 1)}
        self.backend._scan_requested(self.device, object(), ("/device/1", 1))
        self.assertTrue(self.backend.scanning)
        self.assertIsNone(self.backend.last_scan_time)

    def test_old_scan_failure_cannot_end_new_scan(self):
        self.backend.scans = {"/device/1": (10, 2)}
        self.device.request_scan_finish.side_effect = GLib.Error("deferred")
        self.backend._scan_requested(self.device, object(), ("/device/1", 1))
        self.assertTrue(self.backend.scanning)

    def test_scan_timeout_keeps_cached_results(self):
        self.backend.networks = [self.network]
        self.backend.scans = {"/device/1": (10, 1)}
        self.backend._scan_timeout(1)
        self.assertFalse(self.backend.scanning)
        self.assertEqual(self.backend.networks, [self.network])
        self.assertIn("not completed", self.messages[-1][0])

    def test_scans_paused_during_connection(self):
        self.backend.action = {"key": self.network.key}
        self.backend.scan()
        self.device.request_scan_async.assert_not_called()

    def test_stale_ap_does_not_connect(self):
        self.device.get_access_points.return_value = []
        self.backend.connect_network(self.network)
        self.backend.client.add_and_activate_connection_async.assert_not_called()
        self.assertIsNone(self.backend.action)
        self.assertTrue(self.messages[-1][1])

    def test_saved_profile_activation_has_no_scan_or_disconnect(self):
        profile = MagicMock()
        profile.get_path.return_value = "/profile/1"
        self.device.get_available_connections.return_value = [profile]
        ap = MagicMock()
        ap.get_path.return_value = "/ap/1"
        from dataclasses import replace
        network = replace(self.network, profile_path="/profile/1")
        with patch.object(self.backend, "resolve", return_value=(self.device, ap)), patch("quickwifi.backend.GLib.timeout_add_seconds"):
            self.backend.connect_network(network)
        self.backend.client.activate_connection_async.assert_called_once()
        self.backend.client.add_and_activate_connection_async.assert_not_called()
        self.device.request_scan_async.assert_not_called()
        self.device.disconnect_async.assert_not_called()

    def test_activation_reply_does_not_claim_connected_until_activated(self):
        active = MagicMock()
        active.get_state.return_value = NM.ActiveConnectionState.ACTIVATING
        self.backend.action = {"key": self.network.key, "name": "example", "active": active,
                               "started": time.monotonic(), "kind": "connect"}
        self.backend._check_action()
        self.assertIsNotNone(self.backend.action)
        self.assertEqual(self.messages, [])
        active.get_state.return_value = NM.ActiveConnectionState.ACTIVATED
        self.backend._check_action()
        self.assertIsNone(self.backend.action)
        self.assertIn("Connected", self.messages[-1][0])

    def test_failed_activation_preserves_retry_identity(self):
        active = MagicMock()
        active.get_state.return_value = NM.ActiveConnectionState.DEACTIVATED
        active.get_state_reason.return_value = NM.ActiveConnectionStateReason.NO_SECRETS
        self.backend.action = {"key": self.network.key, "name": "example", "active": active}
        self.backend._check_action()
        self.assertIsNone(self.backend.action)
        self.assertEqual(self.backend.failed_key, self.network.key)
        self.assertTrue(self.messages[-1][1])


if __name__ == "__main__":
    unittest.main()
