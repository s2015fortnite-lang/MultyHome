"""Чтение известных полей состояния Станции. Команды управления здесь нет."""


def station_state_lines(capability_type, state):
    if not isinstance(state, dict):
        return []
    name = capability_type.removeprefix("devices.capabilities.")
    # Выводим только известные настройки, не весь ответ с идентификаторами.
    fields = {
        "equalizer": {
            "enabled": "Эквалайзер", "smart_enabled": "Умный эквалайзер",
            "prevent_clipping": "Защита от искажений",
        },
        "audio_player": {
            "state_type": "Состояние плеера", "title": "Название воспроизведения",
            "subtitle": "Исполнитель / описание", "playback_speed": "Скорость воспроизведения",
            "quality": "Качество воспроизведения",
        },
        "voice_activity_detector": {"enabled": "Обнаружение голоса"},
        "speaker_do_not_disturb": {
            "is_do_not_disturb_enabled": "Не беспокоить", "start_time": "Начало режима",
            "end_time": "Конец режима", "decrease_brightness": "Снижение яркости",
        },
        "localization": {"country": "Страна", "locale": "Язык"},
        "stereo_pair": {
            "role": "Роль в стереопаре", "channel": "Канал стереопары",
            "connectivity": "Соединение стереопары", "status": "Состояние стереопары",
        },
        "kids_pro": {
            "kids_search_enabled": "Детский поиск", "kids_content_enabled": "Детский контент",
        },
        "assistant_response_style": {"response_style": "Стиль ответов Алисы"},
    }
    lines = []
    for field, label in fields.get(name, {}).items():
        if field not in state:
            continue
        value = state[field]
        if value is None:
            value = "неизвестно"
        elif isinstance(value, bool):
            value = "включено" if value else "выключено"
        elif not isinstance(value, (str, int, float)):
            continue
        lines.append(f"{label}: {value}")
    if name == "speaker_do_not_disturb":
        for section, field, label in [
            ("features", "allow_incoming_calls", "Входящие звонки в режиме «Не беспокоить»"),
            ("platform_settings", "show_clock", "Показ часов в режиме «Не беспокоить»"),
            ("platform_settings", "show_idle", "Показ заставки в режиме «Не беспокоить»"),
        ]:
            settings = state.get(section)
            if isinstance(settings, dict) and isinstance(settings.get(field), bool):
                value = "включено" if settings[field] else "выключено"
                lines.append(f"{label}: {value}")
    return lines
