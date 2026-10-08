"""Простой Telegram-бот. Запуск: python bot.py."""

import argparse
import os
import time
from pathlib import Path

import requests
from dotenv import load_dotenv

from accounts import Accounts

from yandex_home import DemoHome, HomeError, describe_device, supports_on_off


HELP = """Команды:
/id — ваш Telegram ID
/connect — подключить свой Яндекс Дом
/disconnect — удалить подключение из бота
/devices — список устройств
/status 1 — состояние устройства №1
/on 1 — включить устройство №1
/off 1 — выключить устройство №1

Сначала выполните /devices. Номер берётся из этого списка."""


class Commands:
    def __init__(self, home):
        self.home = home
        self.devices = []

    def handle(self, text):
        parts = text.split()
        if not parts:
            return HELP
        command = parts[0].split("@")[0].lower()
        if command in ("/start", "/help"):
            return HELP
        if command == "/devices":
            # Очищаем старые номера, даже если обновление списка не удалось.
            self.devices = []
            self.devices = self.home.list_devices()
            if not self.devices:
                return "В этом Яндекс Доме нет устройств."
            lines = []
            for number, device in enumerate(self.devices, start=1):
                available = "вкл/выкл" if supports_on_off(device) else "просмотр состояния"
                lines.append(f"{number}. {device.get('name', 'Без названия')} ({available})")
            return "\n".join(lines)
        if command not in ("/on", "/off", "/status"):
            return "Неизвестная команда.\n" + HELP
        if not self.devices:
            return "Сначала выполните /devices."
        if len(parts) != 2 or not parts[1].isdigit():
            return f"Укажите номер устройства. Например: {command} 1"
        number = int(parts[1])
        if number < 1 or number > len(self.devices):
            return "Такого номера нет. Выполните /devices."
        device = self.devices[number - 1]
        if command == "/status":
            return describe_device(self.home.get_device(device["id"]))
        if not supports_on_off(device):
            return "Устройство не поддерживает включение/выключение через Яндекс API."
        self.home.switch(device["id"], command == "/on")
        return f"{device.get('name', 'Устройство')}: команда выполнена."


def telegram_request(token, method, data):
    try:
        response = requests.post(
            f"https://api.telegram.org/bot{token}/{method}", json=data, timeout=40
        )
        if response.status_code != 200:
            raise HomeError(f"Ошибка Telegram API: HTTP {response.status_code}.")
        result = response.json()
    except (requests.RequestException, ValueError):
        # Не выводим текст исключения: URL Telegram содержит токен бота.
        raise HomeError("Нет связи с Telegram или получен некорректный ответ.") from None
    if not result.get("ok"):
        raise HomeError("Telegram отклонил запрос.")
    return result["result"]


def message_reply(message, accounts, sessions):
    # Каждый пользователь управляет только своим домом в личном чате.
    if message.get("chat", {}).get("type") != "private":
        return None
    user_id = message.get("from", {}).get("id")
    text = message.get("text", "")
    parts = text.split()
    command = parts[0].split("@")[0].lower() if parts else ""
    if command == "/id":
        return f"Ваш Telegram ID: {user_id}"
    if command in ("/start", "/help"):
        return HELP
    if command == "/connect":
        return accounts.connect(user_id)
    if command == "/disconnect":
        sessions.pop(user_id, None)
        return accounts.disconnect(user_id)
    if command == "/code":
        if len(parts) != 2:
            return "Формат: /code КОД_ИЗ_ЯНДЕКСА"
        sessions.pop(user_id, None)
        return accounts.authorize(user_id, parts[1])
    if not text:
        return "Отправьте текстовую команду. /help — список команд."
    # Сначала проверяем наличие подключения, затем берём персональный список.
    home = accounts.home(user_id)
    if user_id not in sessions:
        sessions[user_id] = Commands(home)
    return sessions[user_id].handle(text)


def run_bot(token, accounts):
    # getMe проверяет токен. Удаление webhook требуется для режима getUpdates.
    telegram_request(token, "getMe", {})
    telegram_request(token, "deleteWebhook", {"drop_pending_updates": True})
    sessions = {}
    offset = 0
    print("Бот запущен. Откройте личный чат с ним в Telegram. Остановка: Ctrl+C.", flush=True)
    while True:
        try:
            updates = telegram_request(token, "getUpdates", {
                "offset": offset, "timeout": 25, "allowed_updates": ["message"]
            })
            for update in updates:
                # Запоминаем событие до отправки ответа, чтобы при сетевой ошибке
                # случайно не выполнить одну команду управления дважды.
                offset = update["update_id"] + 1
                message = update.get("message")
                if not message:
                    continue
                try:
                    parts = message.get("text", "").split()
                    if parts and parts[0].split("@")[0].lower() == "/code":
                        # Пытаемся убрать одноразовый код из истории переписки.
                        try:
                            telegram_request(token, "deleteMessage", {
                                "chat_id": message["chat"]["id"],
                                "message_id": message["message_id"],
                            })
                        except HomeError:
                            pass
                    reply = message_reply(message, accounts, sessions)
                except HomeError as error:
                    reply = str(error)
                if reply:
                    # Telegram ограничивает длину сообщения; длинный список делим.
                    for start in range(0, len(reply), 4000):
                        telegram_request(token, "sendMessage", {
                            "chat_id": message["chat"]["id"], "text": reply[start:start + 4000]
                        })
        except HomeError as error:
            print(error, flush=True)
            time.sleep(3)


def main():
    parser = argparse.ArgumentParser(description="Учебный бот для Яндекс Дома")
    parser.add_argument("--demo-console", action="store_true", help="Проверка без Telegram и токенов")
    args = parser.parse_args()
    load_dotenv(Path(__file__).with_name(".env"))
    if args.demo_console:
        commands = Commands(DemoHome())
        print("Учебный режим. /help — справка, exit — выход.")
        while True:
            try:
                text = input("> ")
            except EOFError:
                break
            if text.strip() == "exit":
                break
            print(commands.handle(text))
        return
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        raise HomeError("Заполните TELEGRAM_BOT_TOKEN в файле .env.")
    demo_mode = os.getenv("DEMO_MODE", "1")
    if demo_mode not in ("0", "1"):
        raise HomeError("DEMO_MODE должен быть 0 или 1.")
    if demo_mode == "1":
        print("Режим: виртуальные устройства.")
    else:
        print("Режим: каждый пользователь подключает свой настоящий дом.")
    accounts = Accounts(
        demo_mode == "1", os.getenv("YANDEX_CLIENT_ID", "").strip(),
        os.getenv("YANDEX_CLIENT_SECRET", "").strip(),
    )
    if demo_mode == "0" and (not accounts.client_id or not accounts.client_secret):
        raise HomeError("Заполните YANDEX_CLIENT_ID и новый YANDEX_CLIENT_SECRET в .env.")
    run_bot(token, accounts)


if __name__ == "__main__":
    try:
        main()
    except HomeError as error:
        print(error)
        raise SystemExit(1)
    except KeyboardInterrupt:
        print("\nБот остановлен.")
