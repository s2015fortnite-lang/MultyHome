import copy
import io
import json
import stat
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import Mock, patch

import requests

from accounts import Accounts
from bot import Commands
from station_api import DemoStation, StationAPI, StationStore
from station_login import bind_station
from yandex_home import DemoHome, HomeError


def configuration():
    return {
        "unrelated_setting": {"enabled": True},
        "equalizer": {"enabled": False, "custom_preset_bands": [0] * 5,
            "bands": [{"freq": freq, "width": 90, "gain": 0} for freq in (60, 230, 910, 3600, 14000)],
            "smart_enabled": True,
        },
    }


class StationAPITests(unittest.TestCase):
    def setUp(self):
        self.api = StationAPI("Session_id=fake-local-cookie", ["station"])

    def test_cookie_is_scoped_to_secure_yandex_domains(self):
        cookies = list(self.api.session.cookies)
        self.assertEqual(len(cookies), 1)
        self.assertEqual(cookies[0].domain, ".yandex.ru")
        self.assertTrue(cookies[0].secure)
        self.assertNotIn("Cookie", self.api.session.headers)

    def test_invalid_cookie_is_rejected_without_printing_it(self):
        for cookie in ("", "Session_id=private\nCookie: wrong", "bad@key=private-cookie"):
            with self.subTest(cookie_shape=len(cookie)):
                with self.assertRaises(HomeError) as error:
                    StationAPI(cookie)
                self.assertNotIn("private-cookie", str(error.exception))

    def test_missing_csrf_stops_instead_of_printing_page_or_posting(self):
        response = Mock(text="private-cookie private-page")
        with patch.object(self.api, "http", return_value=response) as request:
            with self.assertRaises(HomeError) as error:
                self.api.set_config("station", configuration(), "v1")
        self.assertEqual(request.call_count, 1)
        self.assertEqual(request.call_args.args[0], "GET")
        self.assertNotIn("private-", str(error.exception))

    def test_missing_enabled_is_unknown_not_switched_off(self):
        with patch.object(self.api, "get_config", return_value=({"equalizer": {}}, "v1")):
            self.assertIn("неизвестно", self.api.equalizer("station"))

    def test_equalizer_switch_preserves_other_settings_and_version(self):
        config = configuration()
        original = copy.deepcopy(config)
        with patch.object(self.api, "get_config", return_value=(config, "version-1")):
            with patch.object(self.api, "set_config") as save:
                self.api.equalizer("station", enabled=True)
        device_id, updated, version = save.call_args.args
        self.assertEqual((device_id, version), ("station", "version-1"))
        self.assertTrue(updated["equalizer"]["enabled"])
        expected = copy.deepcopy(original)
        expected["equalizer"]["enabled"] = True
        self.assertEqual(updated, expected)
        self.assertEqual(config, original)

    def test_custom_bands_preserve_frequencies_and_other_configuration(self):
        config = configuration()
        gains = [-3, 0, 1, 0, 3]
        with patch.object(self.api, "get_config", return_value=(config, "version-2")):
            with patch.object(self.api, "set_config") as save:
                self.api.equalizer("station", gains=gains)
        updated = save.call_args.args[1]
        self.assertEqual(updated["unrelated_setting"], config["unrelated_setting"])
        self.assertEqual([band["freq"] for band in updated["equalizer"]["bands"]], [60, 230, 910, 3600, 14000])
        self.assertEqual([band["gain"] for band in updated["equalizer"]["bands"]], gains)
        self.assertEqual(updated["equalizer"]["custom_preset_bands"], gains)
        self.assertEqual(updated["equalizer"]["active_preset_id"], "custom")
        self.assertTrue(updated["equalizer"]["smart_enabled"])

    def test_invalid_gains_do_not_read_or_write_config(self):
        with patch.object(self.api, "get_config") as read:
            with patch.object(self.api, "set_config") as save:
                for gains in ([0], [7] * 5, [0.5] * 5, [True] * 5):
                    with self.assertRaises(HomeError):
                        self.api.equalizer("station", gains=gains)
                read.assert_not_called()
                save.assert_not_called()

    def test_missing_equalizer_or_unknown_bands_do_not_write(self):
        for config in ({}, {"equalizer": {"bands": []}}):
            with patch.object(self.api, "get_config", return_value=(config, "v1")):
                with patch.object(self.api, "set_config") as save:
                    with self.assertRaises(HomeError):
                        self.api.equalizer("station", gains=[0] * 5)
                    save.assert_not_called()

    def test_foreign_device_is_rejected_before_http_request(self):
        with patch.object(self.api, "request") as request:
            with self.assertRaises(HomeError):
                self.api.equalizer("foreign-station", enabled=True)
            request.assert_not_called()

    def test_get_config_uses_confirmed_method(self):
        with patch.object(self.api, "request", return_value={
            "status": "ok", "quasar_config": configuration(), "quasar_config_version": "v3",
        }) as request:
            config, version = self.api.get_config("station")
        request.assert_called_once_with("GET", "/m/v2/user/devices/station/configuration")
        self.assertEqual(version, "v3")
        self.assertIn("equalizer", config)

    def test_post_uses_csrf_and_configuration_version(self):
        self.api.csrf = "fake-csrf"
        response = Mock(status_code=200)
        response.json.return_value = {"status": "ok"}
        with patch.object(self.api.session, "request", return_value=response) as request:
            self.api.set_config("station", configuration(), "v4")
        self.assertEqual(request.call_args.args, ("POST", "https://iot.quasar.yandex.ru/m/v3/user/devices/station/configuration/quasar"))
        self.assertEqual(request.call_args.kwargs["json"]["version"], "v4")
        self.assertEqual(request.call_args.kwargs["headers"], {"x-csrf-token": "fake-csrf"})
        self.assertFalse(request.call_args.kwargs["allow_redirects"])

    def test_csrf_is_loaded_from_authorized_page(self):
        response = Mock(text='{"csrfToken2":"fake-csrf-value"}')
        with patch.object(self.api, "http", return_value=response) as request:
            self.api.load_csrf()
        request.assert_called_once_with("GET", "https://yandex.ru/quasar")
        self.assertEqual(self.api.csrf, "fake-csrf-value")

    def test_expired_session_and_server_rejection_are_not_success(self):
        for status in (302, 401, 403, 500):
            with patch.object(self.api.session, "request", return_value=Mock(status_code=status)):
                with self.assertRaises(HomeError):
                    self.api.get_config("station")
        response = Mock(status_code=200)
        response.json.return_value = {"status": "error", "message": "private-cookie"}
        with patch.object(self.api.session, "request", return_value=response):
            with self.assertRaises(HomeError) as error:
                self.api.get_config("station")
        self.assertNotIn("private-cookie", str(error.exception))

    def test_network_error_does_not_print_credentials(self):
        output = io.StringIO()
        with redirect_stdout(output):
            with patch.object(self.api.session, "request", side_effect=requests.ConnectionError("private-cookie")):
                with self.assertRaises(HomeError) as error:
                    self.api.get_config("station")
        self.assertNotIn("private-cookie", output.getvalue() + str(error.exception))

    def test_browser_cookie_for_different_home_is_not_saved(self):
        public = Mock()
        public.list_devices.return_value = [{"id": "mine", "capabilities": [{"type": "devices.capabilities.equalizer"}]}]
        native = Mock()
        native.list_devices.return_value = [{"id": "someone-else"}]
        store = Mock(data={})
        with patch("station_login.StationAPI", return_value=native):
            with self.assertRaises(HomeError):
                bind_station(123, "private-cookie", public, store)
        self.assertEqual(store.data, {})
        store.save.assert_not_called()

    def test_binding_saves_only_devices_from_this_users_oauth_home(self):
        public = Mock()
        public.list_devices.return_value = [{"id": "mine", "capabilities": [{"type": "devices.capabilities.equalizer"}]}]
        native = Mock()
        native.list_devices.return_value = [{"id": "mine"}, {"id": "another"}]
        store = Mock(data={})
        with patch("station_login.StationAPI", return_value=native):
            self.assertEqual(bind_station(123, "fake-cookie", public, store), 1)
        self.assertEqual(store.data["123"]["device_ids"], ["mine"])
        native.get_config.assert_called_once_with("mine")
        native.load_csrf.assert_called_once()
        store.save.assert_called_once()

    def test_connections_and_disconnect_are_separate_for_users(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "station_sessions.json"
            store = StationStore(path)
            store.data = {
                "1": {"cookie": "Session_id=fake-first", "device_ids": ["first"]},
                "2": {"cookie": "Session_id=fake-second", "device_ids": ["second"]},
            }
            store.save()
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            restored = StationStore(path)
            self.assertEqual(restored.connection(1).device_ids, {"first"})
            self.assertEqual(restored.connection(2).device_ids, {"second"})
            self.assertIsNone(restored.connection(3))
            restored.remove(1)
            self.assertNotIn("1", StationStore(path).data)
            self.assertIn("2", StationStore(path).data)

    def test_demo_equalizer_commands_and_private_config_diagnostics(self):
        home = DemoHome()
        commands = Commands(home, DemoStation(home))
        commands.handle("/devices")
        self.assertIn("выключен", commands.handle("/station 5"))
        self.assertIn("принял", commands.handle("/equalizer 5 on"))
        self.assertIn("включено", commands.handle("/status 5"))
        self.assertIn("принял", commands.handle("/eqbands 5 -3 0 0 0 3"))
        self.assertIn("-3, 0, 0, 0, 3", commands.handle("/station 5"))
        report = commands.handle("/station_info 5")
        self.assertIn("<number>", report)
        self.assertNotIn("14000", report)
        self.assertIn("/equalizer 5", commands.handle("/capabilities 5"))

    def test_unconnected_user_gets_local_setup_instruction(self):
        commands = Commands(DemoHome())
        commands.handle("/devices")
        self.assertIn("station_login.py", commands.handle("/station 5"))

    def test_demo_equalizer_is_isolated_between_telegram_users(self):
        with tempfile.TemporaryDirectory() as directory:
            accounts = Accounts(True, path=Path(directory) / "users.json")
            first = Commands(accounts.home(1), accounts.station(1))
            second = Commands(accounts.home(2), accounts.station(2))
            first.handle("/devices")
            second.handle("/devices")
            first.handle("/equalizer 5 on")
            self.assertIn("включено", first.handle("/status 5"))
            self.assertIn("выключено", second.handle("/status 5"))


if __name__ == "__main__":
    unittest.main()
