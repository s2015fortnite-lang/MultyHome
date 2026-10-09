import unittest

from yandex_home import describe_device


class StationStateTests(unittest.TestCase):
    def test_station_state_without_standard_value_is_displayed(self):
        device = {"name": "Станция", "capabilities": [
            {"type": "devices.capabilities.equalizer", "state": {
                "prevent_clipping": True, "enabled": False, "smart_enabled": True,
            }},
            {"type": "devices.capabilities.audio_player", "state": {
                "state_type": "playing", "title": "Учебный трек", "subtitle": "Исполнитель",
                "playback_speed": 1, "quality": "high", "stream_id": "private-stream",
            }},
            {"type": "devices.capabilities.kids_pro", "state": {
                "kids_search_enabled": False, "kids_content_enabled": True,
            }},
        ]}
        reply = describe_device(device)
        self.assertIn("Эквалайзер: выключено", reply)
        self.assertIn("Умный эквалайзер: включено", reply)
        self.assertIn("Название воспроизведения: Учебный трек", reply)
        self.assertIn("Детский поиск: выключено", reply)
        self.assertNotIn("private-stream", reply)
        self.assertNotIn("не передал значения", reply)

    def test_dnd_nested_fields_are_displayed(self):
        reply = describe_device({"capabilities": [{
            "type": "devices.capabilities.speaker_do_not_disturb", "state": {
                "is_do_not_disturb_enabled": False, "start_time": "23:00", "end_time": "07:00",
                "features": {"allow_incoming_calls": True},
                "platform_settings": {"show_clock": True, "show_idle": False},
                "decrease_brightness": True,
            },
        }]})
        self.assertIn("Не беспокоить: выключено", reply)
        self.assertIn("Начало режима: 23:00", reply)
        self.assertIn("Входящие звонки", reply)
        self.assertIn("Показ заставки в режиме «Не беспокоить»: выключено", reply)

    def test_null_state_and_unknown_fields_do_not_crash_or_leak(self):
        reply = describe_device({"capabilities": [
            {"type": "devices.capabilities.equalizer", "state": None},
            {"type": "devices.capabilities.voice_enrollment", "state": {"person": "private-person"}},
            {"type": "devices.capabilities.phone_calls", "state": {"accounts": ["private-account"]}},
            {"type": "devices.capabilities.stereo_pair", "state": {"partner_endpoint_id": "private-partner"}},
        ]})
        self.assertNotIn("private-", reply)
        self.assertIn("не передал значения", reply)

    def test_null_setting_is_unknown_not_disabled(self):
        reply = describe_device({"capabilities": [{
            "type": "devices.capabilities.equalizer", "state": {"enabled": None},
        }]})
        self.assertIn("Эквалайзер: неизвестно", reply)
        self.assertNotIn("выключено", reply)

    def test_native_and_standard_fields_can_coexist(self):
        reply = describe_device({"capabilities": [
            {"type": "devices.capabilities.range", "state": {"instance": "volume", "value": 30}},
            {"type": "devices.capabilities.assistant_response_style", "state": {"response_style": "brief"}},
        ]})
        self.assertIn("volume: 30", reply)
        self.assertIn("Стиль ответов Алисы: brief", reply)


if __name__ == "__main__":
    unittest.main()
