import json
import unittest
from unittest.mock import patch

from bot import Commands
from capabilities import STATION_CAPABILITIES, capability_info, describe_capabilities
from yandex_home import DemoHome


class StationInfoTests(unittest.TestCase):
    def test_all_reported_station_types_get_honest_explanation(self):
        device = {"name": "Колонка", "capabilities": [
            {"type": "devices.capabilities." + name, "state": None}
            for name in STATION_CAPABILITIES
        ]}
        reply = describe_capabilities(device, 2)
        for label in STATION_CAPABILITIES.values():
            self.assertIn(label, reply)
        self.assertIn("/capability_info 2", reply)
        self.assertNotIn("управление пока не реализовано", reply)
        self.assertIn("не подтверждён", reply)

    def test_report_hides_values_and_credentials(self):
        device = {"name": "private-name", "id": "private-device", "capabilities": [{
            "type": "devices.capabilities.audio_player", "retrievable": True,
            "parameters": {"instance": "private-instance", "token": "private-token",
                "options": [{"title": "private-title", "id": "private-person"}]},
            "state": {"instance": "private-state", "value": {"volume": 42,
                "phone": "private-phone", "secret": "private-secret", "user123": "private-value"}},
        }]}
        report = capability_info(device)
        self.assertNotIn("private-", report)
        self.assertNotIn("user123", report)
        data = json.loads(report.split("\n", 1)[1])
        self.assertEqual(data["capabilities"][0]["state_shape"]["value"]["volume"], "<number>")
        self.assertEqual(data["capabilities"][0]["parameters_shape"]["token"], "<hidden>")
        self.assertTrue(data["capabilities"][0]["retrievable"])

    def test_null_data_is_explicit(self):
        report = capability_info({"capabilities": [{"type": "devices.capabilities.equalizer",
            "parameters": None, "state": None}]})
        data = json.loads(report.split("\n", 1)[1])
        self.assertIsNone(data["capabilities"][0]["parameters_shape"])
        self.assertIsNone(data["capabilities"][0]["state_shape"])

    def test_read_only_command_never_sends_action(self):
        home = DemoHome()
        commands = Commands(home)
        commands.handle("/devices")
        with patch.object(home, "set_capability") as action:
            self.assertIn("parameters_shape", commands.handle("/capability_info 1"))
            action.assert_not_called()

    def test_excessive_nesting_and_dynamic_keys_are_hidden(self):
        device = {"capabilities": [{"type": "devices.capabilities.voice_enrollment",
            "parameters": {"records": {"email@example.com": "private-value"}},
            "state": {"a": {"b": {"c": {"d": {"e": {"f": "private-data"}}}}}}}]}
        report = capability_info(device)
        self.assertNotIn("email@example.com", report)
        self.assertNotIn("private-", report)
        self.assertIn("<nested data>", report)


if __name__ == "__main__":
    unittest.main()
