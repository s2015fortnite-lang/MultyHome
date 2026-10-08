"""Связь Telegram ID с токеном Яндекс Дома конкретного пользователя."""

import json
import os
import time
from pathlib import Path
from urllib.parse import urlencode

import requests

from yandex_home import DemoHome, HomeError, YandexHome


class Accounts:
    def __init__(self, demo, client_id="", client_secret="", path=None):
        self.demo = demo
        self.client_id = client_id
        self.client_secret = client_secret
        self.path = path or Path(__file__).with_name("users.json")
        self.waiting = {}
        self.demo_homes = {}
        # Файл содержит секреты. Не выводим его содержимое в логи.
        if self.path.exists():
            try:
                self.tokens = json.loads(self.path.read_text(encoding="utf-8"))
                if not isinstance(self.tokens, dict):
                    raise ValueError
            except (ValueError, OSError):
                raise HomeError("Не удалось прочитать users.json. Проверьте файл, не удаляя его.") from None
        else:
            self.tokens = {}

    def save(self):
        temporary = self.path.with_suffix(".tmp")
        # Создаём файл, доступный только владельцу процесса на Linux/macOS.
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as file:
            json.dump(self.tokens, file)
        temporary.replace(self.path)

    def home(self, user_id):
        if self.demo:
            if user_id not in self.demo_homes:
                self.demo_homes[user_id] = DemoHome()
            return self.demo_homes[user_id]
        token = self.tokens.get(str(user_id))
        if not token:
            raise HomeError("Сначала подключите свой Яндекс Дом командой /connect.")
        return YandexHome(token)

    def connect(self, user_id):
        if self.demo:
            return "Сейчас учебный режим: /devices покажет ваши виртуальные устройства."
        if not self.client_id or not self.client_secret:
            raise HomeError("Владелец бота должен заполнить YANDEX_CLIENT_ID и YANDEX_CLIENT_SECRET в .env.")
        params = {
            "response_type": "code", "client_id": self.client_id,
            "redirect_uri": "https://oauth.yandex.ru/verification_code",
            "scope": "iot:view iot:control",
        }
        self.waiting[user_id] = time.time() + 300
        return (
            "Откройте ссылку и войдите в свой аккаунт Яндекса:\n"
            "https://oauth.yandex.ru/authorize?" + urlencode(params) +
            "\n\nРазрешите доступ к своему дому. Затем отправьте одноразовый код: /code КОД"
            "\nСделайте это в течение 5 минут. Секрет приложения и токен доступа сюда не отправляйте."
        )

    def authorize(self, user_id, code):
        if self.demo:
            raise HomeError("В учебном режиме авторизация не требуется.")
        if self.waiting.pop(user_id, 0) < time.time():
            raise HomeError("Сначала выполните /connect. Запрос подключения действует 5 минут.")
        try:
            response = requests.post("https://oauth.yandex.ru/token", data={
                "grant_type": "authorization_code", "code": code,
                "client_id": self.client_id, "client_secret": self.client_secret,
                "redirect_uri": "https://oauth.yandex.ru/verification_code",
            }, timeout=20)
            if response.status_code != 200:
                raise HomeError("Яндекс отклонил код. Выполните /connect ещё раз и получите новый код.")
            token = response.json().get("access_token")
            if not token:
                raise HomeError("Яндекс не вернул токен доступа.")
        except (requests.RequestException, ValueError):
            raise HomeError("Не удалось получить токен Яндекса. Повторите /connect.") from None
        # До сохранения проверяем, что токен позволяет прочитать дом.
        YandexHome(token).list_devices()
        self.tokens[str(user_id)] = token
        try:
            self.save()
        except OSError:
            self.tokens.pop(str(user_id), None)
            raise HomeError("Не удалось сохранить подключение. Проверьте права на users.json.") from None
        return "Ваш Яндекс Дом подключён. Выполните /devices."

    def disconnect(self, user_id):
        self.waiting.pop(user_id, None)
        self.demo_homes.pop(user_id, None)
        self.tokens.pop(str(user_id), None)
        try:
            self.save()
        except OSError:
            raise HomeError("Не удалось удалить подключение из файла. Сообщите владельцу бота.") from None
        return "Подключение удалено из бота. Доступ приложения также можно отозвать в Яндекс ID."
