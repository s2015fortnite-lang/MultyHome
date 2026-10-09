"""Отдельный неофициальный API конфигурации Станции: пока только эквалайзер.

Форматы GET/POST сверены с открытой интеграцией AlexxIT/YandexStation.
Сессия одного пользователя не используется для устройств другого пользователя.
"""

import copy
import json
import os
import re
from http.cookies import CookieError, SimpleCookie
from pathlib import Path
from urllib.parse import quote

import requests

from yandex_home import HomeError


class StationStore:
    def __init__(self, path=None):
        self.path = path or Path(__file__).with_name("station_sessions.json")
        try:
            self.data = json.loads(self.path.read_text()) if self.path.exists() else {}
            if not isinstance(self.data, dict):
                raise ValueError
        except (ValueError, OSError):
            raise HomeError("Не удалось прочитать station_sessions.json. Не удаляйте файл вслепую.") from None

    def save(self):
        temporary = self.path.with_suffix(".tmp")
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as file:
            if hasattr(os, "fchmod"):
                os.fchmod(file.fileno(), 0o600)
            json.dump(self.data, file)
        temporary.replace(self.path)

    def connection(self, user_id):
        settings = self.data.get(str(user_id))
        if not settings:
            return None
        if not isinstance(settings, dict) or not isinstance(settings.get("cookie"), str) or not isinstance(settings.get("device_ids"), list):
            raise HomeError("Некорректная запись подключения Станции. Повторите station_login.py.")
        if not all(isinstance(device_id, str) and device_id for device_id in settings["device_ids"]):
            raise HomeError("Некорректный список Станций. Повторите station_login.py.")
        return StationAPI(settings["cookie"], settings["device_ids"])

    def remove(self, user_id):
        if str(user_id) in self.data:
            del self.data[str(user_id)]
            self.save()


class StationAPI:
    def __init__(self, cookie, device_ids=None):
        if "\n" in cookie or "\r" in cookie:
            raise HomeError("Введите только значение Cookie одной строкой.")
        if cookie.lower().startswith("cookie:"):
            cookie = cookie.split(":", 1)[1].strip()
        parsed = SimpleCookie()
        try:
            parsed.load(cookie)
        except (CookieError, ValueError, TypeError):
            raise HomeError("Не удалось разобрать Cookie. Проверьте локально введённую строку.") from None
        if not parsed:
            raise HomeError("Cookie пуст или имеет неверный формат.")
        self.session = requests.Session()
        for name, item in parsed.items():
            self.session.cookies.set(name, item.value, domain=".yandex.ru", path="/", secure=True)
        self.device_ids = set(device_ids or [])
        self.csrf = None

    def http(self, method, url, data=None, headers=None):
        try:
            response = self.session.request(method, url, json=data, headers=headers, timeout=20, allow_redirects=False)
        except requests.RequestException:
            raise HomeError("Нет связи с API Станции. Проверьте сеть и доступ к iot.quasar.yandex.ru.") from None
        if response.status_code in (301, 302, 303, 307, 308, 401, 403):
            raise HomeError("Сессия Станции отклонена или истекла. Повторите station_login.py локально.")
        if response.status_code != 200:
            raise HomeError(f"API Станции вернул HTTP {response.status_code}. Неофициальный метод мог измениться.")
        return response

    def request(self, method, path, data=None):
        headers = None
        if method != "GET":
            if not self.csrf:
                self.load_csrf()
            headers = {"x-csrf-token": self.csrf}
        response = self.http(method, "https://iot.quasar.yandex.ru" + path, data, headers)
        try:
            result = response.json()
        except ValueError:
            raise HomeError("API Станции вернул некорректный ответ.") from None
        if not isinstance(result, dict) or result.get("status") != "ok":
            raise HomeError("API Станции не подтвердил запрос. Ответ с секретными данными не выводится.")
        return result

    def load_csrf(self):
        # CSRF берётся из авторизованной страницы; TLS-проверка остаётся включённой.
        response = self.http("GET", "https://yandex.ru/quasar")
        match = re.search(r'"csrfToken2"\s*:\s*("(?:\\.|[^"\\])*")', response.text)
        if not match:
            raise HomeError("Не удалось получить CSRF-токен Станции. Сессия истекла или формат страницы изменился.")
        try:
            self.csrf = json.loads(match.group(1))
        except ValueError:
            raise HomeError("Не удалось прочитать CSRF-токен Станции.") from None

    def list_devices(self):
        result = self.request("GET", "/m/v3/user/devices")
        devices = []
        for household in result.get("households") or []:
            devices.extend(household.get("all") or [])
        return devices

    def check_device(self, device_id):
        if device_id not in self.device_ids:
            raise HomeError("Эта Станция не привязана к вашему подключению. Повторите station_login.py для своего Telegram ID.")

    def get_config(self, device_id):
        self.check_device(device_id)
        result = self.request("GET", f"/m/v2/user/devices/{quote(device_id, safe='')}/configuration")
        config = result.get("quasar_config")
        version = result.get("quasar_config_version")
        if not isinstance(config, dict) or version is None:
            raise HomeError("API не вернул конфигурацию и её версию. Настройки не изменялись.")
        return config, version

    def set_config(self, device_id, config, version):
        self.check_device(device_id)
        self.request("POST", f"/m/v3/user/devices/{quote(device_id, safe='')}/configuration/quasar", {
            "config": config, "version": version,
        })

    def equalizer(self, device_id, enabled=None, gains=None):
        if enabled is not None and not isinstance(enabled, bool):
            raise HomeError("Эквалайзер: используйте on или off.")
        if gains is not None and (len(gains) != 5 or any(type(value) is not int or not -6 <= value <= 6 for value in gains)):
            raise HomeError("Укажите пять целых усилений от -6 до 6 дБ, от низких частот к высоким.")
        original, version = self.get_config(device_id)
        config = copy.deepcopy(original)
        equalizer = config.get("equalizer")
        if not isinstance(equalizer, dict):
            raise HomeError("В конфигурации Станции нет эквалайзера. Откройте его настройки в приложении Яндекса, затем повторите.")
        if enabled is None and gains is None:
            value = equalizer.get("enabled")
            status = "включён" if value is True else "выключен" if value is False else "неизвестно"
            lines = ["Эквалайзер: " + status]
            bands = equalizer.get("bands")
            if isinstance(bands, list):
                values = [str(band.get("gain", "?")) for band in bands if isinstance(band, dict)]
                lines.append("Усиления от низких частот к высоким, дБ: " + ", ".join(values))
            return "\n".join(lines)
        if gains is not None:
            bands = equalizer.get("bands")
            if not isinstance(bands, list) or len(bands) != 5 or not all(isinstance(band, dict) for band in bands):
                raise HomeError("Неизвестный формат полос эквалайзера. Конфигурация не изменялась.")
            for band, gain in zip(bands, gains):
                band["gain"] = gain
            equalizer["custom_preset_bands"] = gains[:]
            equalizer["active_preset_id"] = "custom"
            equalizer["enabled"] = True
        else:
            equalizer["enabled"] = enabled
        # Отправляем конфигурацию вместе с её версией, сохраняя остальные настройки.
        self.set_config(device_id, config, version)
        return "Яндекс принял настройки эквалайзера. Проверьте их через /station и в приложении Яндекса."


class DemoStation(StationAPI):
    def __init__(self, home):
        self.home = home
        self.config = {"equalizer": {"enabled": False,
            "bands": [{"freq": freq, "gain": 0} for freq in (60, 230, 910, 3600, 14000)]}}

    def get_config(self, device_id):
        device = self.home.get_device(device_id)
        if not any(cap.get("type") == "devices.capabilities.equalizer" for cap in device.get("capabilities") or []):
            raise HomeError("Это не учебная Станция с эквалайзером.")
        return copy.deepcopy(self.config), "demo-version"

    def set_config(self, device_id, config, version):
        self.get_config(device_id)
        self.config = copy.deepcopy(config)
        self.home.get_device(device_id)["capabilities"][0]["state"]["enabled"] = config["equalizer"]["enabled"]
