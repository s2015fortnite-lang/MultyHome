import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from accounts import Accounts
from bot import Commands, message_reply, telegram_request
from yandex_home import DemoHome, HomeError, YandexHome


class BotTests(unittest.TestCase):
    def test_switch_and_read_state(self):
        commands = Commands(DemoHome())
        self.assertIn("Сначала", commands.handle("/on 1"))
        self.assertIn("Учебная лампа", commands.handle("/devices"))
        commands.handle("/on 1")
        self.assertIn("on: включено", commands.handle("/status 1"))
        commands.handle("/off 1")
        self.assertIn("on: выключено", commands.handle("/status 1"))

    def test_wrong_number_does_not_control_devices(self):
        commands = Commands(DemoHome())
        commands.handle("/devices")
        for text in ("/on 0", "/on 3", "/on -1", "/on abc"):
            commands.handle(text)
        self.assertIn("on: выключено", commands.handle("/status 1"))

    def test_sensor_cannot_be_switched(self):
        home = DemoHome()
        home.devices = [{"id": "sensor", "name": "Датчик", "capabilities": []}]
        commands = Commands(home)
        commands.handle("/devices")
        with patch.object(home, "switch") as switch:
            self.assertIn("не поддерживает", commands.handle("/on 1"))
            switch.assert_not_called()

    def test_failed_refresh_clears_old_numbers(self):
        home = DemoHome()
        commands = Commands(home)
        commands.handle("/devices")
        with patch.object(home, "list_devices", side_effect=HomeError("Ошибка")):
            with self.assertRaises(HomeError):
                commands.handle("/devices")
        self.assertIn("Сначала", commands.handle("/on 1"))

    def test_users_have_separate_homes_and_numbers(self):
        with tempfile.TemporaryDirectory() as directory:
            accounts = Accounts(True, path=Path(directory) / "users.json")
            sessions = {}

            def message(user, text):
                return {"chat": {"type": "private"}, "from": {"id": user}, "text": text}

            message_reply(message(1, "/devices"), accounts, sessions)
            message_reply(message(1, "/on 1"), accounts, sessions)
            self.assertIn("Сначала", message_reply(message(2, "/status 1"), accounts, sessions))
            message_reply(message(2, "/devices"), accounts, sessions)
            self.assertIn("выключено", message_reply(message(2, "/status 1"), accounts, sessions))
            self.assertIn("включено", message_reply(message(1, "/status 1"), accounts, sessions))

    def test_group_messages_are_ignored(self):
        accounts = Mock()
        reply = message_reply({"chat": {"type": "group"}, "text": "/on 1"}, accounts, {})
        self.assertIsNone(reply)
        accounts.home.assert_not_called()

    def test_unconnected_user_cannot_access_home(self):
        with tempfile.TemporaryDirectory() as directory:
            accounts = Accounts(False, path=Path(directory) / "users.json")
            with self.assertRaises(HomeError):
                accounts.home(123)

    def test_code_requires_connect(self):
        with tempfile.TemporaryDirectory() as directory:
            accounts = Accounts(False, path=Path(directory) / "users.json")
            with patch("accounts.requests.post") as request:
                with self.assertRaises(HomeError):
                    accounts.authorize(123, "fake-code")
                request.assert_not_called()

    def test_authorization_saved_only_for_requesting_user(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "users.json"
            accounts = Accounts(False, "test-client", "test-secret", path)
            accounts.connect(123)
            response = Mock(status_code=200)
            response.json.return_value = {"access_token": "test-token-not-real"}
            with patch("accounts.requests.post", return_value=response):
                with patch.object(YandexHome, "list_devices", return_value=[]):
                    accounts.authorize(123, "test-code")
            restored = Accounts(False, path=path)
            self.assertEqual(restored.home(123).token, "test-token-not-real")
            with self.assertRaises(HomeError):
                restored.home(456)
            restored.disconnect(123)
            self.assertEqual(Accounts(False, path=path).tokens, {})

    def test_yandex_action_payload(self):
        response = Mock(status_code=200)
        response.json.return_value = {
            "status": "ok", "devices": [{"id": "lamp", "capabilities": [{
                "type": "devices.capabilities.on_off",
                "state": {"instance": "on", "action_result": {"status": "DONE"}},
            }]}],
        }
        with patch("yandex_home.requests.request", return_value=response) as request:
            YandexHome("fake-token").switch("lamp", True)
        data = request.call_args.kwargs["json"]
        self.assertEqual(data["devices"][0]["id"], "lamp")
        self.assertEqual(data["devices"][0]["actions"][0]["state"], {"instance": "on", "value": True})

    def test_yandex_device_error_is_not_success(self):
        response = Mock(status_code=200)
        response.json.return_value = {
            "status": "ok", "devices": [{"id": "lamp", "capabilities": [{
                "type": "devices.capabilities.on_off",
                "state": {"instance": "on", "action_result": {"status": "ERROR"}},
            }]}],
        }
        with patch("yandex_home.requests.request", return_value=response):
            with self.assertRaises(HomeError):
                YandexHome("fake-token").switch("lamp", True)

    def test_telegram_network_error_does_not_expose_token(self):
        import requests
        with patch("bot.requests.post", side_effect=requests.ConnectionError("URL with secret token")):
            with self.assertRaises(HomeError) as error:
                telegram_request("fake-token", "getMe", {})
        self.assertNotIn("secret", str(error.exception))


if __name__ == "__main__":
    unittest.main()
