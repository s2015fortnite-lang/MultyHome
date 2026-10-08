import copy
import unittest
from unittest.mock import Mock, patch

from bot import Commands
from capabilities import COLOR, MODE, RANGE, prepare_action
from yandex_home import DemoHome, HomeError, YandexHome


class CapabilityTests(unittest.TestCase):
    def setUp(self):
        self.home = DemoHome()
        self.commands = Commands(self.home)
        self.commands.handle("/devices")

    def test_complete_demo_workflow(self):
        for command, number, expected in [
            ("/brightness 1 70", 1, "brightness: 70"),
            ("/color 1 #FF0000", 1, "rgb: 16711680"),
            ("/white 1 4000", 1, "temperature_k: 4000"),
            ("/scene 1 reading", 1, "scene: reading"),
            ("/temperature 3 22,5", 3, "temperature: 22.5"),
            ("/mode 3 thermostat cool", 3, "thermostat: cool"),
            ("/volume 4 40", 4, "volume: 40"),
            ("/range 4 volume 50", 4, "volume: 50"),
        ]:
            with self.subTest(command=command):
                self.assertIn("выполнена", self.commands.handle(command))
                self.assertIn(expected, self.commands.handle(f"/status {number}"))

    def test_capability_help_comes_from_device_parameters(self):
        lamp = self.commands.handle("/capabilities 1")
        self.assertIn("/brightness 1", lamp)
        self.assertIn("2700–6500", lamp)
        self.assertIn("reading, party", lamp)
        climate = self.commands.handle("/capabilities 3")
        self.assertIn("шаг 0.5", climate)
        self.assertIn("auto, cool, heat", climate)
        speaker = self.commands.handle("/capabilities 4")
        self.assertIn("/volume 4", speaker)
        self.assertNotIn("/color", speaker)

    def test_bad_inputs_never_send_action(self):
        original = copy.deepcopy(self.home.devices)
        for command in [
            "/brightness 1 101", "/brightness 1 0", "/brightness 1 NaN",
            "/brightness 1 Infinity", "/brightness 1 1e999999",
            "/brightness 1 0.000000000000000000001", "/temperature 3 22.25",
            "/temperature 3 31", "/color 1 #GG0000", "/color 1 12345",
            "/color 2 FF0000", "/white 1 1000", "/white 1 4000.5",
            "/mode 3 thermostat madeup", "/mode 3 fan_speed auto",
            "/scene 1 madeup", "/scene 2 reading", "/brightness 4 50",
        ]:
            with self.subTest(command=command):
                with patch.object(self.home, "set_capability") as action:
                    with self.assertRaises(HomeError):
                        self.commands.handle(command)
                    action.assert_not_called()
        self.assertEqual(self.home.devices, original)

    def test_name_does_not_determine_controls(self):
        self.home.devices[0]["name"] = "Кондиционер"
        self.home.devices[0]["type"] = "devices.types.thermostat.ac"
        self.commands.handle("/brightness 1 65")
        self.assertIn("brightness: 65", self.commands.handle("/status 1"))
        with self.assertRaises(HomeError):
            self.commands.handle("/temperature 1 22")

    def test_sensor_reading_is_not_temperature_control(self):
        self.home.devices[1]["properties"] = [{
            "type": "devices.properties.float", "state": {"instance": "temperature", "value": 22},
        }]
        with patch.object(self.home, "set_capability") as action:
            with self.assertRaises(HomeError):
                self.commands.handle("/temperature 2 25")
            action.assert_not_called()

    def test_fresh_capabilities_are_checked_before_sending(self):
        self.home.devices[0]["capabilities"] = []
        with patch.object(self.home, "set_capability") as action:
            with self.assertRaises(HomeError):
                self.commands.handle("/brightness 1 50")
            action.assert_not_called()

    def test_retrievable_false_still_allows_control(self):
        self.home.devices[0]["capabilities"][1]["retrievable"] = False
        self.home.devices[0]["capabilities"][1]["state"] = None
        self.commands.handle("/brightness 1 40")
        self.assertIn("brightness: 40", self.commands.handle("/status 1"))

    def test_relative_only_range_is_not_sent_as_absolute(self):
        self.home.devices[3]["capabilities"][0]["parameters"]["random_access"] = False
        with patch.object(self.home, "set_capability") as action:
            with self.assertRaises(HomeError):
                self.commands.handle("/volume 4 30")
            action.assert_not_called()
        self.assertIn("относительное", self.commands.handle("/capabilities 4"))

    def test_hsv_device_receives_hsv_not_rgb(self):
        self.home.devices[0]["capabilities"][2]["parameters"]["color_model"] = "hsv"
        self.commands.handle("/color 1 00FF00")
        state = self.home.devices[0]["capabilities"][2]["state"]
        self.assertEqual(state, {"instance": "hsv", "value": {"h": 120, "s": 100, "v": 100}})

    def test_rgb_missing_model_does_not_send_action(self):
        del self.home.devices[0]["capabilities"][2]["parameters"]["color_model"]
        with self.assertRaises(HomeError):
            self.commands.handle("/color 1 FF0000")
        self.commands.handle("/white 1 4000")

    def test_empty_parameters_and_null_states_do_not_crash(self):
        self.home.devices[0]["capabilities"] = [
            {"type": RANGE, "parameters": None, "state": None},
            {"type": MODE, "parameters": {"instance": "thermostat", "modes": None}},
            {"type": COLOR, "parameters": {"color_scene": None}},
        ]
        self.assertIn("доступные команды", self.commands.handle("/capabilities 1"))
        with self.assertRaises(HomeError):
            self.commands.handle("/brightness 1 50")

    def test_brightness_protocol_limits_without_explicit_range(self):
        lamp = {"capabilities": [{"type": RANGE, "parameters": {"instance": "brightness"}}]}
        self.assertEqual(prepare_action(lamp, "/brightness", ["50"]), (RANGE, "brightness", 50))
        for value in ("-1", "101"):
            with self.assertRaises(HomeError):
                prepare_action(lamp, "/brightness", [value])

    def test_wrong_command_format_does_not_send_action(self):
        with patch.object(self.home, "set_capability") as action:
            for text in ("/color 1", "/mode 3 cool", "/brightness x 50", "/volume 4 50 extra"):
                self.assertIn("Неверный формат", self.commands.handle(text))
            action.assert_not_called()

    def test_yandex_payloads_for_all_new_settings(self):
        home = YandexHome("fake-token")
        cases = [
            (RANGE, "brightness", 50), (RANGE, "temperature", 22.5),
            (MODE, "thermostat", "cool"), (COLOR, "rgb", 16711680),
            (COLOR, "hsv", {"h": 120, "s": 100, "v": 100}),
            (COLOR, "temperature_k", 4000), (COLOR, "scene", "reading"),
        ]
        for capability_type, instance, value in cases:
            with self.subTest(instance=instance):
                response = Mock(status_code=200)
                response.json.return_value = {"status": "ok", "devices": [{
                    "id": "device", "capabilities": [{"type": capability_type,
                    "state": {"instance": instance, "action_result": {"status": "DONE"}}}],
                }]}
                with patch.object(home.session, "request", return_value=response) as request:
                    home.set_capability("device", capability_type, instance, value)
                action = request.call_args.kwargs["json"]["devices"][0]["actions"][0]
                expected = {"instance": instance, "value": value}
                if capability_type == RANGE:
                    expected["relative"] = False
                self.assertEqual(action, {"type": capability_type, "state": expected})

    def test_wrong_instance_confirmation_is_not_success(self):
        response = Mock(status_code=200)
        response.json.return_value = {"status": "ok", "devices": [{
            "id": "device", "capabilities": [{"type": RANGE,
            "state": {"instance": "volume", "action_result": {"status": "DONE"}}}],
        }]}
        with patch("yandex_home.requests.Session.request", return_value=response):
            with self.assertRaises(HomeError):
                YandexHome("fake-token").set_capability("device", RANGE, "brightness", 50)


if __name__ == "__main__":
    unittest.main()
